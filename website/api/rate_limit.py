"""IP-based daily free-tier limiter."""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import date


class RateLimiter:
    """Thread-safe per-IP daily rate limiter stored in memory.

    Paid account credits are handled separately by the account store.
    For production, replace with Redis-backed counting.
    """

    def __init__(self, free_limit: int = 20) -> None:
        self.free_limit = free_limit
        self._lock = threading.Lock()
        self._usage: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def _today(self) -> str:
        return date.today().isoformat()

    def limit_for(self, ip: str) -> int:
        """Return the daily image limit for an IP."""
        return self.free_limit

    def remaining(self, ip: str) -> int:
        limit = self.limit_for(ip)
        today = self._today()
        with self._lock:
            # Inline cleanup: drop stale date keys for this IP
            ip_data = self._usage[ip]
            stale = [k for k in ip_data if k != today]
            for k in stale:
                del ip_data[k]
            used = ip_data.get(today, 0)
            return max(0, limit - used)

    def consume(self, ip: str, count: int = 1) -> None:
        with self._lock:
            self._usage[ip][self._today()] += count

    def reset_old(self) -> None:
        """Remove entries older than today to prevent memory growth."""
        today = self._today()
        with self._lock:
            for ip in list(self._usage.keys()):
                self._usage[ip] = defaultdict(
                    int,
                    {k: v for k, v in self._usage[ip].items() if k == today},
                )
                if not self._usage[ip]:
                    del self._usage[ip]
