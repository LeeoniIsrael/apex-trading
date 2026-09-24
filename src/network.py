"""Small synchronous JSON transport with retries and a per-provider circuit breaker."""

from __future__ import annotations

import threading
import time
from typing import Any

import requests


class CircuitOpenError(RuntimeError):
    pass


class ResilientJSONSession:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        headers: dict[str, str] | None = None,
        max_attempts: int = 3,
        failure_threshold: int = 5,
        recovery_seconds: float = 60.0,
        backoff_seconds: float = 0.25,
        session: requests.Session | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.backoff_seconds = backoff_seconds
        self.session = session or requests.Session()
        if headers:
            self.session.headers.update(headers)
        self._failures = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def get(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        with self._lock:
            if time.monotonic() < self._open_until:
                raise CircuitOpenError(f"upstream circuit open for {url}")
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout_seconds)
                if response.status_code in {429, 500, 502, 503, 504}:
                    response.raise_for_status()
                response.raise_for_status()
                payload = response.json()
                with self._lock:
                    self._failures = 0
                    self._open_until = 0.0
                return payload
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    time.sleep(min(self.backoff_seconds * 2**attempt, 2.0))
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._open_until = time.monotonic() + self.recovery_seconds
        raise RuntimeError(f"JSON upstream failed after {self.max_attempts} attempts: {url}") from last_error
