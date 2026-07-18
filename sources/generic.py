"""
sources/generic.py — Fallback source for any novel website.

Uses a multi-strategy extraction approach:
1. Known CSS selectors (common novel site patterns)
2. trafilatura (general article extraction)
3. BS4 heuristic (largest text block)

This source always matches (supports() returns True) and serves as the
last resort when no site-specific source is registered.
"""

import logging
from typing import Optional

from sources.base import ChapterText, NovelSource
from scraping.parser import parser, DEFAULT_CONTENT_SELECTORS
from scraping.normalizer import normalizer, DEFAULT_NOISE_SELECTORS

logger = logging.getLogger(__name__)


class GenericSource(NovelSource):
    """Fallback source that handles any novel website with generic extraction."""

    name = "generic"
    domain_patterns = []  # matches everything (supports always returns True)
    requires_browser = False

    @classmethod
    def supports(cls, url: str) -> bool:
        return True

    def get_content_selectors(self) -> list[str]:
        return DEFAULT_CONTENT_SELECTORS

    def get_noise_selectors(self) -> list[str]:
        return DEFAULT_NOISE_SELECTORS

    def get_chapter_title(self, html: str) -> Optional[str]:
        return parser.extract_title(html)

    def get_next_chapter_url(self, html: str, current_url: str) -> Optional[str]:
        return parser.detect_next_chapter_url(html, current_url)

    def get_novel_info(self, html: str, chapter_url: str) -> dict:
        return parser.detect_novel_info(html, chapter_url)

    def extract(self, html: str, url: str) -> ChapterText:
        """Multi-strategy extraction: try all methods, keep the best result."""
        selectors = self.get_content_selectors()
        noise = self.get_noise_selectors()

        candidates = []

        # Strategy 1: known CSS selectors
        text = parser.extract_with_selectors(html, selectors, noise)
        if text and len(text) > 200:
            candidates.append(text)

        # Strategy 2: trafilatura
        text = parser.extract_with_trafilatura(html)
        if text and len(text) > 200:
            candidates.append(text)

        # Strategy 3: BS4 heuristic
        text = parser.extract_with_bs4_heuristic(html)
        if text and len(text) > 200:
            candidates.append(text)

        # Pick the longest candidate
        text = max(candidates, key=len) if candidates else ""
        text = normalizer.normalize(text)

        title = self.get_chapter_title(html)
        next_url = self.get_next_chapter_url(html, url)
        novel_info = self.get_novel_info(html, url)

        return ChapterText(
            title=title or "Untitled Chapter",
            text=text,
            word_count=normalizer.word_count(text),
            source_url=url,
            next_chapter_url=next_url,
            novel_title=novel_info.get("title"),
            novel_index_url=novel_info.get("index_url"),
        )
