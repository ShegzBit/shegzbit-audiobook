"""
scraping/fetcher.py — Unified HTTP + browser page fetching.

Tries lightweight HTTP first (curl_cffi with chrome impersonation).
Falls back to Playwright when the site requires JavaScript or returns
a challenge page.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from curl_cffi import requests as cffi_requests
from curl_cffi.requests.exceptions import HTTPError, RequestException

from errors import CaptchaError, HttpError, NetworkError, ScrapingError
from scraping.detector import detector

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


@dataclass
class FetchResult:
    """Result of a page fetch."""

    html: str
    status_code: int
    used_browser: bool


class Fetcher:
    """Unified fetcher that tries HTTP then browser fallback.

    Usage:
        fetcher = Fetcher(browser_manager)
        result = fetcher.fetch("https://example.com/chapter-1")
    """

    def __init__(self, browser_manager=None):
        self._browser = browser_manager

    def fetch(self, url: str, requires_browser: bool = False) -> FetchResult:
        """Fetch a URL and return the HTML.

        Args:
            url: The URL to fetch.
            requires_browser: If True, skip HTTP and go straight to browser.

        Returns:
            FetchResult with HTML content and metadata.

        Raises:
            CaptchaError: If both HTTP and browser return challenge pages.
            HttpError: On HTTP errors (4xx/5xx).
            NetworkError: On connection failures.
        """
        if requires_browser:
            return self._fetch_via_browser(url)

        return self._fetch_via_http_with_browser_fallback(url)

    def _fetch_via_http_with_browser_fallback(self, url: str) -> FetchResult:
        """Try HTTP first, fall back to browser on captcha/failure."""
        try:
            resp = cffi_requests.get(url, headers=HEADERS, timeout=20, impersonate="chrome")
            resp.raise_for_status()
            html = resp.text

            if not detector.is_captcha_page(html):
                return FetchResult(html=html, status_code=resp.status_code, used_browser=False)

            logger.info("HTTP got challenge page for %s, falling back to browser", url)

        except HTTPError as e:
            status_code = e.response.status_code if e.response is not None else 0
            if status_code in (403, 429):
                logger.info("HTTP %d for %s, trying browser fallback", status_code, url)
            else:
                raise HttpError(status_code, url=url) from e

        except RequestException as e:
            logger.info("HTTP request failed (%s), trying browser fallback for %s", e, url)

        if self._browser:
            return self._fetch_via_browser(url)

        raise NetworkError(
            "No browser available for fallback and HTTP fetch failed",
            url=url,
        )

    def _fetch_via_browser(self, url: str) -> FetchResult:
        """Fetch using Playwright browser."""
        if not self._browser:
            raise NetworkError("Browser manager not available", url=url)

        html = self._browser.fetch(url)
        if detector.is_captcha_page(html):
            raise CaptchaError(
                "Browser also returned a challenge page. "
                "The site may require manual intervention.",
                url=url,
            )
        return FetchResult(html=html, status_code=200, used_browser=True)
