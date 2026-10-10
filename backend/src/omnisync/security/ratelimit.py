"""In-memory sliding-window rate limiter (05-API-SPEC section 6).

A single server process serves one instance (ADR-0004), so process memory is the
correct scope; limits reset on restart, which is acceptable for these windows.
"""

from __future__ import annotations

import math
import threading
from collections import defaultdict, deque

from omnisync.clock import Clock


class RateLimiter:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, bucket: str, key: str, limit: int, window_s: float) -> int | None:
        """Record one request. Returns ``None`` if allowed, else seconds until retry."""
        now = self._clock.now().timestamp()
        with self._lock:
            hits = self._hits[(bucket, key)]
            while hits and hits[0] <= now - window_s:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, math.ceil(hits[0] + window_s - now))
            hits.append(now)
            return None
