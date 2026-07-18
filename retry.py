"""
retry.py — Configurable retry decorator with exponential backoff and jitter.
"""

import logging
import random
import time
from functools import wraps

from errors import ScrapingError

logger = logging.getLogger(__name__)


def retry(
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    backoff_max: float = 60.0,
    retryable_errors: tuple = (ScrapingError,),
):
    """Decorator that retries a function on recoverable errors.

    Args:
        max_attempts: Total attempts (1 = no retry, 2 = one retry, etc.).
        backoff_base: Base multiplier for exponential backoff.
        backoff_max: Maximum wait time in seconds.
        retryable_errors: Tuple of exception types to retry on.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_errors as exc:
                    last_exc = exc
                    recoverable = getattr(exc, "recoverable", True)
                    if not recoverable or attempt == max_attempts:
                        raise
                    wait = min(backoff_base ** attempt + random.uniform(0, 1), backoff_max)
                    logger.warning(
                        "Attempt %d/%d failed (%s), retrying in %.1fs...",
                        attempt, max_attempts, exc, wait,
                    )
                    time.sleep(wait)
            raise last_exc  # unreachable but satisfies type checkers

        return wrapper

    return decorator
