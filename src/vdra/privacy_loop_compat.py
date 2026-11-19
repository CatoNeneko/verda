"""Keep sanitizer cache mutually exclusive across HTTP and worker event loops."""

import asyncio
import functools
import threading


class CrossLoopLock:
    def __init__(self):
        self._lock = threading.Lock()

    async def __aenter__(self):
        while not self._lock.acquire(blocking=False):
            await asyncio.sleep(0.005)
        return self

    async def __aexit__(self, *args):
        self._lock.release()


def install():
    from openviking.privacy.sanitizer import PrivacySanitizer

    original = PrivacySanitizer.__init__
    if getattr(original, "_cross_loop_compat", False):
        return

    @functools.wraps(original)
    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self._cache_lock = CrossLoopLock()

    init._cross_loop_compat = True
    PrivacySanitizer.__init__ = init
