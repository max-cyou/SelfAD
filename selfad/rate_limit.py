import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._entries: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            entries = self._entries[key]
            while entries and entries[0] <= cutoff:
                entries.popleft()
            if len(entries) >= limit:
                return False
            entries.append(now)
            if len(self._entries) > 10_000:
                self._cleanup(cutoff)
            return True

    def _cleanup(self, cutoff: float) -> None:
        for key in tuple(self._entries):
            entries = self._entries[key]
            while entries and entries[0] <= cutoff:
                entries.popleft()
            if not entries:
                del self._entries[key]


rate_limiter = SlidingWindowRateLimiter()


def client_key(host: str | None, scope: str) -> str:
    return f"{scope}:{host or 'unknown'}"
