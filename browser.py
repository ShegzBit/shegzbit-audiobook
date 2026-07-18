"""
browser.py — Persistent Playwright Chromium browser for fetching pages.
Uses a single browser instance across requests for performance.
"""

import asyncio
import logging
import re
import threading
import time
from typing import Optional

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

CHALLENGE_PATTERNS = [
    "just a moment", "checking your browser", "cf-challenge", "cf-turnstile",
    "security verification", "verifies you are not a bot",
    "verifies you're not a bot", "security service to protect",
    "checking if the site connection is secure",
    "please wait while we verify", "browser verification",
    "enable javascript and cookies", "ddos-guard",
]


def _is_challenge_page(html: str) -> bool:
    lower = html.lower()
    return any(pat in lower for pat in CHALLENGE_PATTERNS)


class BrowserManager:
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

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
        context = await self._browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            viewport=DEFAULT_VIEWPORT,
            locale="en-US",
        )
        await context.add_init_script(STEALTH_SCRIPT)
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            # Wait for challenge to auto-resolve (up to 15s)
            deadline = time.time() + 15
            while time.time() < deadline:
                content = await page.content()
                if not _is_challenge_page(content):
                    break
                logger.info("Challenge page detected, waiting for auto-resolve...")
                await asyncio.sleep(2)
            else:
                logger.warning("Challenge did not auto-resolve within 15s for %s", url)

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
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(self._start(), self._loop)
        future.result(timeout=30)

    def fetch(self, url: str, timeout_ms: int = 30000) -> str:
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(self._fetch(url, timeout_ms), self._loop)
        return future.result(timeout=timeout_ms / 1000 + 25)

    def shutdown(self):
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
