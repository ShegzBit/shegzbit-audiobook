"""
browser.py — Persistent Playwright Chromium browser for fetching pages.

Maintains a single browser instance with reusable contexts to minimize
memory usage. Contexts are pooled and recycled across requests.
"""

import asyncio
import logging
import threading
import time
from typing import Optional

from scraping.detector import detector

logger = logging.getLogger(__name__)

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = { runtime: {} };
"""

DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_CHALLENGE_TIMEOUT = 15


class BrowserManager:
    """Persistent Chromium browser with context reuse.

    - Single browser instance shared across all fetches
    - Contexts are created per-fetch but reuse the same browser process
    - Challenge wait time is configurable
    - Thread-safe via asyncio event loop in a background thread
    """

    def __init__(self, challenge_timeout: int = DEFAULT_CHALLENGE_TIMEOUT):
        self._playwright = None
        self._browser = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._challenge_timeout = challenge_timeout

    def _ensure_loop(self):
        if self._loop is not None and self._loop.is_running():
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    async def _start(self):
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        logger.info("Playwright Chromium browser launched")

    async def _fetch(self, url: str, timeout_ms: int = 30000) -> str:
        """Fetch a URL using a browser context.

        Creates a fresh context per request to isolate state (cookies, cache).
        Reuses the same browser process to avoid cold-start overhead.
        """
        context = await self._browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            viewport=DEFAULT_VIEWPORT,
            locale="en-US",
        )
        await context.add_init_script(STEALTH_SCRIPT)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            # Wait for challenge to auto-resolve (configurable timeout)
            deadline = time.time() + self._challenge_timeout
            while time.time() < deadline:
                content = await page.content()
                if not detector.is_captcha_page(content):
                    break
                logger.info("Challenge page detected, waiting for auto-resolve...")
                await asyncio.sleep(2)
            else:
                logger.warning("Challenge did not auto-resolve within %ds for %s",
                               self._challenge_timeout, url)

            return await page.content()
        finally:
            await page.close()
            await context.close()

    async def _shutdown(self):
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Playwright browser shut down")

    def start(self):
        """Launch the browser. Call once at application startup."""
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(self._start(), self._loop)
        future.result(timeout=30)

    def fetch(self, url: str, timeout_ms: int = 30000) -> str:
        """Fetch a URL synchronously. Called from worker threads."""
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(self._fetch(url, timeout_ms), self._loop)
        return future.result(timeout=timeout_ms / 1000 + 25)

    def shutdown(self):
        """Shut down the browser. Call at application exit."""
        if self._loop is None or self._loop.is_closed():
            return
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
        try:
            future.result(timeout=10)
        except Exception:
            logger.exception("Error shutting down browser")
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None


manager = BrowserManager()
