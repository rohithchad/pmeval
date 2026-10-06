"""HTTP client with rate limiting, retries and exponential backoff.

Every API client in this project goes through HttpClient so that politeness
(rate limits) and resilience (retries) are implemented once.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any

import requests

logger = logging.getLogger(__name__)

# 429 = rate limited, 5xx = server trouble. Both are worth retrying.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class HttpError(Exception):
    """Raised when a request fails permanently (non-retryable status or retries exhausted)."""


class HttpClient:
    """Thin wrapper over requests.Session returning decoded JSON.

    Args:
        base_url: prefix for all request paths.
        min_interval_seconds: minimum gap between two requests (simple rate limit).
        max_retries: how many times to retry a retryable failure.
        backoff_seconds: first retry delay; doubles on each further retry.
        timeout_seconds: per-request timeout.
        sleep: injectable sleep function so tests do not actually wait.
        clock: injectable monotonic clock for the same reason.
    """

    def __init__(
        self,
        base_url: str,
        min_interval_seconds: float = 0.2,
        max_retries: int = 5,
        backoff_seconds: float = 1.0,
        timeout_seconds: float = 30.0,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.min_interval_seconds = min_interval_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET base_url + path and return the parsed JSON body."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(self.max_retries + 1):
            self._wait_for_rate_limit()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout_seconds)
            except (requests.ConnectionError, requests.Timeout) as error:
                self._backoff_or_raise(attempt, f"network error: {type(error).__name__}")
                continue
            if response.status_code in RETRYABLE_STATUS:
                self._backoff_or_raise(attempt, f"HTTP {response.status_code}")
                continue
            if response.status_code >= 400:
                raise HttpError(f"HTTP {response.status_code} for {url}")
            return response.json()
        raise HttpError(f"retries exhausted for {url}")  # pragma: no cover

    def _wait_for_rate_limit(self) -> None:
        """Sleep just long enough to keep min_interval_seconds between requests."""
        now = self._clock()
        if self._last_request_at is not None:
            wait = self.min_interval_seconds - (now - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._clock()

    def _backoff_or_raise(self, attempt: int, reason: str) -> None:
        """Sleep with exponential backoff, or raise if no retries remain."""
        if attempt >= self.max_retries:
            raise HttpError(f"giving up after {attempt + 1} attempts ({reason})")
        delay = self.backoff_seconds * (2**attempt)
        delay += random.uniform(0, delay * 0.1)  # jitter avoids synchronized retries
        logger.warning("retrying in %.1fs after %s", delay, reason)
        self._sleep(delay)
