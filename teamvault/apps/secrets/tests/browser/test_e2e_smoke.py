"""E2E smoke tests against a *deployed* TeamVault instance over HTTPS.

Unlike :class:`PlaywrightTestCase` (which boots Django's in-process live test
server with a throwaway superuser), these tests drive the real deployment —
the login form, LDAP authentication, CSRF protection, and the ingress path —
exactly as a browser would. That is what makes them the right safety net for
deployment-level regressions (login/CSRF breaks that only appear behind the
proxy) that the in-process suite can never catch.

They are skipped unless ``TEAMVAULT_E2E_URL`` is set, so the default
``manage.py test`` run stays green in environments with no target instance.

Run against the dev deployment (credentials resolved from TeamVault via the
CLI, never committed):

    TEAMVAULT_E2E_URL=https://teamvault.dev.nuke.benjamin-borbe.de \\
    TEAMVAULT_E2E_USER=testuser \\
    TEAMVAULT_E2E_PASSWORD="$(teamvault-cli password teamvault-dev-testuser)" \\
    uv run teamvault/manage.py test teamvault.apps.secrets.tests.browser.test_e2e_smoke

The test user is provisioned in the dev instance's LDAP and must be a member
of ``ou=employees`` (the ``LDAP_REQUIRE_GROUP``) — see the dev bootstrap
runbook.
"""

import os
import unittest

from playwright.sync_api import Error as PlaywrightError, sync_playwright

BASE_URL = os.environ.get('TEAMVAULT_E2E_URL', '').rstrip('/')
USERNAME = os.environ.get('TEAMVAULT_E2E_USER', 'testuser')
PASSWORD = os.environ.get('TEAMVAULT_E2E_PASSWORD', '')


@unittest.skipUnless(BASE_URL, 'set TEAMVAULT_E2E_URL to run e2e against a deployed instance')
class E2ESmokeTests(unittest.TestCase):
    """Authenticated page-load smoke checks against a deployed instance.

    Captures any JS ``pageerror``, ``console.error``, or non-success HTTP
    response during navigation and asserts a clean page load, mirroring the
    in-process suite. Login success is asserted by the redirect leaving
    ``/login/``.
    """

    @classmethod
    def setUpClass(cls):
        cls._playwright = sync_playwright().start()
        cls.browser = cls._playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.js_errors: list[str] = []
        self.page.on('pageerror', lambda exc: self.js_errors.append(f'pageerror: {exc}'))
        self.page.on(
            'console',
            lambda msg: self.js_errors.append(f'console.error: {msg.text}') if msg.type == 'error' else None,
        )
        self.page.on(
            'response',
            lambda resp: self.js_errors.append(f'http {resp.status}: {resp.url}') if resp.status >= 400 else None,  # noqa: PLR2004
        )
        self._login()

    def tearDown(self):
        self.page.close()

    def _login(self):
        """Submit the real login form and require the post-login redirect."""
        self.page.goto(f'{BASE_URL}/login/?next=/', wait_until='domcontentloaded')
        self.page.wait_for_selector('input[name="username"]', state='attached', timeout=30_000)
        self.page.fill('input[name="username"]', USERNAME)
        self.page.fill('input[name="password"]', PASSWORD)
        self.page.click('button[type="submit"]')
        self.page.wait_for_load_state('networkidle')
        # Login success == the browser left /login/ (either an auth "didn't
        # match" page or a CSRF 403 keeps it there).
        self.assertNotIn('/login/', self.page.url, f'login failed, still on {self.page.url}')
        # Login-flow JS is exercised by the redirect assert, not per-page smokes.
        self.js_errors.clear()

    def smoke(self, path: str, dom_selector: str = 'footer'):
        """Navigate to ``path`` and assert no JS errors and a known element rendered."""
        self.js_errors.clear()
        self.page.goto(f'{BASE_URL}{path}', wait_until='networkidle')
        try:
            self.page.wait_for_selector(dom_selector, state='attached', timeout=5_000)
        except PlaywrightError as exc:
            raise AssertionError(f'expected element {dom_selector!r} not found on {path}: {exc}') from exc
        self.assertEqual([], self.js_errors, f'JS errors on {path}: {self.js_errors}')

    def test_login_redirects_to_dashboard(self):
        self.assertEqual(f'{BASE_URL}/', self.page.url)

    def test_dashboard(self):
        self.smoke('/')

    def test_secret_list(self):
        self.smoke('/secrets/')

    def test_user_settings(self):
        self.smoke('/settings/')

    def test_user_list(self):
        self.smoke('/users/')

    def test_user_detail(self):
        self.smoke(f'/users/{USERNAME}/')
