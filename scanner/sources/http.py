"""Throttled, retrying HTTP client with a browser-like User-Agent (NSE blocks bots)."""
from __future__ import annotations

import logging
import threading
import time

import requests
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class SourceError(RuntimeError):
    """Source returned something unusable (bad payload, missing columns)."""


class RetryableHTTPError(requests.HTTPError):
    pass


class HttpClient:
    def __init__(self, min_interval_s: float = 1.0, timeout_s: float = 30, retries: int = 4,
                 headers: dict | None = None):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": BROWSER_UA,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            **(headers or {}),
        })
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self.retries = retries
        self._last = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        with self._lock:
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def _get_once(self, url: str, **kw) -> requests.Response:
        self._throttle()
        resp = self.session.get(url, timeout=self.timeout_s, **kw)
        if resp.status_code in (429, 500, 502, 503, 504):
            raise RetryableHTTPError(f"{resp.status_code} for {url}", response=resp)
        resp.raise_for_status()
        return resp

    def get(self, url: str, **kw) -> requests.Response:
        for attempt in Retrying(
            stop=stop_after_attempt(self.retries),
            wait=wait_random_exponential(multiplier=1, max=30),
            retry=retry_if_exception_type(
                (requests.ConnectionError, requests.Timeout, RetryableHTTPError)
            ),
            before_sleep=lambda rs: log.warning(
                "retry %d for %s: %s", rs.attempt_number, url, rs.outcome.exception()
            ),
            reraise=True,
        ):
            with attempt:
                return self._get_once(url, **kw)
        raise AssertionError("unreachable")


class NseClient(HttpClient):
    """nseindia.com API client: warms cookies from the homepage, re-warms on 401/403."""

    HOME = "https://www.nseindia.com/"

    def __init__(self, **kw):
        super().__init__(headers={"Referer": self.HOME}, **kw)
        self._warm = False

    def _warmup(self) -> None:
        try:
            self._throttle()
            self.session.get(self.HOME, timeout=self.timeout_s)  # 403 is fine; cookies still set
        except requests.RequestException as exc:
            log.debug("NSE warmup failed: %s", exc)
        self._warm = True

    def get(self, url: str, **kw) -> requests.Response:
        if not self._warm:
            self._warmup()
        try:
            return super().get(url, **kw)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (401, 403):
                self._warmup()
                return super().get(url, **kw)
            raise
