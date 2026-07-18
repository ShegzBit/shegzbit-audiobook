"""
extractor.py — Pipeline orchestrator for chapter extraction.

This is the single entry point for extracting chapter text from a URL.
It delegates to the source registry and scraping pipeline internally,
but preserves the same public API that worker.py depends on.

Public API:
    extract_chapter(url, html=None) -> ChapterText
    fetch_html(url) -> str
    is_captcha_page(html) -> bool
    CaptchaError (exception for backward compat)
"""

import logging
from typing import Optional

from browser import manager as browser_manager
from errors import CaptchaError, EmptyExtractionError, ScrapingError
from scraping.fetcher import Fetcher
from scraping.detector import detector
from scraping.normalizer import normalizer
from sources import registry
from sources.base import ChapterText

logger = logging.getLogger(__name__)

# Re-export CaptchaError for backward compatibility with worker.py
# (it imports CaptchaError from here)

_fetcher = Fetcher(browser_manager=browser_manager)


def fetch_html(url: str) -> str:
    """Fetch HTML from a URL. Preserved for backward compatibility.

    Uses the new Fetcher internally (HTTP first, browser fallback).
    """
    source = registry.resolve(url)
    result = _fetcher.fetch(url, requires_browser=source.requires_browser)
    return result.html


def is_captcha_page(html: str) -> bool:
    """Check if HTML is a challenge page. Preserved for backward compat."""
    return detector.is_captcha_page(html)


def extract_chapter(url: str, html: Optional[str] = None) -> ChapterText:
    """Extract chapter text from a URL or pre-fetched HTML.

    Pipeline:
    1. Resolve source from URL
    2. Fetch HTML if not provided
    3. Detect challenge pages (early exit)
    4. Extract via source-specific logic
    5. Normalize text
    6. Return ChapterText

    This is the ONLY function worker.py calls.
    """
    # 1. Resolve source
    source = registry.resolve(url)

    # 2. Fetch if needed
    if html is None:
        result = _fetcher.fetch(url, requires_browser=source.requires_browser)
        html = result.html

    # 3. Early challenge detection
    if detector.is_captcha_page(html):
        raise CaptchaError(
            "A captcha/challenge page was detected. "
            "Open the URL in your browser to solve it, then retry — "
            "or paste the page HTML source manually.",
            url=url,
            source_name=source.name,
        )

    # 4. Extract via source
    try:
        chapter = source.extract(html, url)
    except ScrapingError:
        raise
    except Exception as e:
        logger.exception("Extraction failed for %s with source %s", url, source.name)
        raise EmptyExtractionError(
            f"Extraction failed: {e}",
            url=url,
            source_name=source.name,
        ) from e

    # 5. Validate result
    if not chapter.text or len(chapter.text) < 200:
        raise EmptyExtractionError(
            "Could not find substantial chapter text on this page. "
            f"Tried all extraction strategies on: {url}",
            url=url,
            source_name=source.name,
        )

    return chapter
