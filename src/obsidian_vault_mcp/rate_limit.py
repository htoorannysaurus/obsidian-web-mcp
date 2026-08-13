"""Small in-process sliding-window rate limiter for the single-user server."""

import threading
import time
from collections import deque

from .config import RATE_LIMIT_READ, RATE_LIMIT_WRITE


class RateLimiter:
    def __init__(self) -> None:
        self._read: deque[float] = deque()
        self._write: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self, *, write: bool) -> bool:
        now = time.monotonic()
        cutoff = now - 60
        bucket = self._write if write else self._read
        limit = RATE_LIMIT_WRITE if write else RATE_LIMIT_READ
        with self._lock:
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True


rate_limiter = RateLimiter()
