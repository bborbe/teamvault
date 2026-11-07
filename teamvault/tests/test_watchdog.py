import threading
import time
import unittest
from unittest import mock

from teamvault import watchdog


class ThresholdSecondsTest(unittest.TestCase):
    def test_default_when_unset(self):
        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(watchdog.threshold_seconds(), watchdog.DEFAULT_THRESHOLD_SECONDS)

    def test_reads_environment(self):
        with mock.patch.dict('os.environ', {'TEAMVAULT_SLOW_REQUEST_SECONDS': '2.5'}):
            self.assertEqual(watchdog.threshold_seconds(), 2.5)

    def test_invalid_value_falls_back_to_default(self):
        with mock.patch.dict('os.environ', {'TEAMVAULT_SLOW_REQUEST_SECONDS': 'not-a-number'}):
            self.assertEqual(watchdog.threshold_seconds(), watchdog.DEFAULT_THRESHOLD_SECONDS)


class InFlightTrackingTest(unittest.TestCase):
    def setUp(self):
        with watchdog._lock:
            watchdog._in_flight.clear()

    def tearDown(self):
        with watchdog._lock:
            watchdog._in_flight.clear()

    def test_started_then_finished_removes_the_entry(self):
        watchdog.request_started('/some/path')
        self.assertIn(threading.get_ident(), watchdog._in_flight)

        watchdog.request_finished()
        self.assertNotIn(threading.get_ident(), watchdog._in_flight)

    def test_finished_without_started_is_harmless(self):
        watchdog.request_finished()
        self.assertEqual(watchdog._in_flight, {})


class CheckOnceTest(unittest.TestCase):
    def setUp(self):
        with watchdog._lock:
            watchdog._in_flight.clear()

    def tearDown(self):
        with watchdog._lock:
            watchdog._in_flight.clear()

    def _put_in_flight(self, elapsed_seconds, dumped=False):
        ident = threading.get_ident()
        with watchdog._lock:
            watchdog._in_flight[ident] = (time.monotonic() - elapsed_seconds, '/slow', dumped)
        return ident

    def test_fast_request_is_not_dumped(self):
        self._put_in_flight(elapsed_seconds=0)
        with mock.patch.object(watchdog, '_dump') as dump:
            self.assertEqual(watchdog.check_once(), 0)
        dump.assert_not_called()

    def test_slow_request_is_dumped_once(self):
        ident = self._put_in_flight(elapsed_seconds=watchdog.DEFAULT_THRESHOLD_SECONDS + 1)
        with mock.patch.object(watchdog, '_dump') as dump:
            self.assertEqual(watchdog.check_once(), 1)
            # A second pass must not dump the same request again.
            self.assertEqual(watchdog.check_once(), 0)

        dump.assert_called_once()
        args = dump.call_args.args
        self.assertEqual(args[0], ident)
        self.assertEqual(args[1], '/slow')
        self.assertGreater(args[2], watchdog.DEFAULT_THRESHOLD_SECONDS)

    def test_threshold_is_honoured_from_environment(self):
        self._put_in_flight(elapsed_seconds=2)
        with mock.patch.dict('os.environ', {'TEAMVAULT_SLOW_REQUEST_SECONDS': '30'}):
            with mock.patch.object(watchdog, '_dump') as dump:
                self.assertEqual(watchdog.check_once(), 0)
        dump.assert_not_called()
