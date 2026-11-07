"""Dump Python thread stacks when a request runs longer than a threshold.

TeamVault serves through a single gunicorn sync worker, so one slow request
blocks every other caller. When a request exceeds the threshold, this watchdog
writes a full thread-stack dump to stderr, which lands in the pod log — so the
blocking frame is captured *while* the stall is happening instead of being
inferred from the surrounding log lines afterwards.

The threshold is configurable with ``TEAMVAULT_SLOW_REQUEST_SECONDS`` and
defaults to 5 seconds, comfortably above the ~0.2 s a healthy request takes and
below the shortest stall observed in production.
"""

import faulthandler
import logging
import os
import sys
import threading
import time

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD_SECONDS = 5.0
POLL_SECONDS = 0.5

_lock = threading.Lock()
# thread ident -> (started_at, path, already_dumped)
_in_flight: dict[int, tuple[float, str, bool]] = {}
_started = False


def threshold_seconds() -> float:
    """Return the slow-request threshold, falling back to the default."""
    raw = os.environ.get('TEAMVAULT_SLOW_REQUEST_SECONDS')
    if not raw:
        return DEFAULT_THRESHOLD_SECONDS
    try:
        return float(raw)
    except ValueError:
        logger.warning('invalid TEAMVAULT_SLOW_REQUEST_SECONDS=%r, using %s', raw, DEFAULT_THRESHOLD_SECONDS)
        return DEFAULT_THRESHOLD_SECONDS


def _dump(ident: int, path: str, elapsed: float) -> None:
    logger.error(
        'slow request: %s running %.1fs (thread %s) — dumping thread stacks',
        path,
        elapsed,
        ident,
    )
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)


def check_once() -> int:
    """One watchdog pass. Returns how many stacks were dumped."""
    due = []
    with _lock:
        for ident, (started_at, path, dumped) in _in_flight.items():
            elapsed = time.monotonic() - started_at
            if dumped or elapsed <= threshold_seconds():
                continue
            # Mark first so a slow request is dumped once, not every poll.
            _in_flight[ident] = (started_at, path, True)
            due.append((ident, path, elapsed))
    for ident, path, elapsed in due:
        _dump(ident, path, elapsed)
    return len(due)


def _watch() -> None:
    while True:
        time.sleep(POLL_SECONDS)
        check_once()


def start() -> None:
    """Start the watchdog thread once. Safe to call on every request."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    thread = threading.Thread(target=_watch, name='slow-request-watchdog', daemon=True)
    thread.start()


def request_started(path: str) -> None:
    start()
    with _lock:
        _in_flight[threading.get_ident()] = (time.monotonic(), path, False)


def request_finished() -> None:
    with _lock:
        _in_flight.pop(threading.get_ident(), None)
