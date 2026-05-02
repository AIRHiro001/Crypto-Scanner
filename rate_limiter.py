"""
utils/rate_limiter.py
─────────────────────────────────────────────────────────────────────────────
Token-bucket rate limiter to stay within Binance's API weight limits.
Binance Futures REST: 2 400 request-weight per minute.
─────────────────────────────────────────────────────────────────────────────
"""
import time
import threading
from utils.logger import log


class RateLimiter:
    """
    Token-bucket rate limiter.

    Args:
        max_tokens (int):   bucket capacity (= burst limit)
        refill_rate (float): tokens added per second
    """

    def __init__(self, max_tokens: int = 2400, refill_rate: float = 40.0):
        self.max_tokens   = max_tokens
        self.refill_rate  = refill_rate   # tokens / second
        self._tokens      = max_tokens
        self._last_refill = time.monotonic()
        self._lock        = threading.Lock()

    def _refill(self):
        now     = time.monotonic()
        elapsed = now - self._last_refill
        gained  = elapsed * self.refill_rate
        self._tokens      = min(self.max_tokens, self._tokens + gained)
        self._last_refill = now

    def acquire(self, weight: int = 1):
        """Block until `weight` tokens are available."""
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= weight:
                    self._tokens -= weight
                    return
                wait = (weight - self._tokens) / self.refill_rate
            log.debug(f"Rate limit: waiting {wait:.2f}s for {weight} tokens")
            time.sleep(wait)


# Singleton instance used project-wide
rate_limiter = RateLimiter()
