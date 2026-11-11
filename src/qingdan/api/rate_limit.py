from collections import OrderedDict, deque
import threading
import time


class RateLimiter:
    """Bounded per-peer rolling-minute limiter; forwarded headers are never keys."""
    def __init__(self):
        self._entries = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, peer: str, limit: int, window: float=60) -> bool:
        now = time.monotonic()
        with self._lock:
            samples = self._entries.setdefault(peer,deque())
            self._entries.move_to_end(peer)
            while samples and samples[0] <= now-window:
                samples.popleft()
            if len(samples) >= limit:
                return False
            samples.append(now)
            while len(self._entries)>2048:
                self._entries.popitem(last=False)
            return True
