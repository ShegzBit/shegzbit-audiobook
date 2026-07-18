"""
sources/base.py — Abstract base class for novel sources and shared data types.

Every novel website gets a subclass of NovelSource. The registry auto-discovers
subclasses at import time, so adding a new site is just creating a new file.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ChapterText:
    """Structured output from chapter extraction."""

    title: str
    text: str
    word_count: int
    source_url: str
    next_chapter_url: Optional[str] = None
    novel_title: Optional[str] = None
    novel_index_url: Optional[str] = None
    source_name: Optional[str] = None


class NovelSource(ABC):
    """Interface that every novel source must implement.

    Attributes:
        name: Short identifier (e.g. "wuxia_world").
        domain_patterns: List of domain strings to match against.
            Use exact domains ("wuxiaworld.com") or glob-style ("*.wuxiaworld.com").
        requires_browser: If True, always use Playwright for this source.
            If False, try HTTP first and fall back to browser on captcha.
        language: ISO 639-1 language code. Used for TTS voice selection hints.
    """

    name: str = ""
    domain_patterns: list[str] = field(default_factory=list)
    requires_browser: bool = False
    language: str = "en"

    @classmethod
    @abstractmethod
    def supports(cls, url: str) -> bool:
        """Return True if this source can handle the given URL.

        Typically checks whether the URL's domain matches domain_patterns.
        The registry calls this in order; first match wins.
        """

    @abstractmethod
    def extract(self, html: str, url: str) -> ChapterText:
        """Extract chapter content from fetched HTML.

        Implementations should:
        1. Parse the HTML
        2. Locate the chapter content using site-specific selectors
        3. Extract title, text, next chapter URL, and novel metadata
        4. Return a ChapterText with clean text

        This method receives already-fetched HTML. It does NOT fetch.
        """

    def get_chapter_title(self, html: str) -> Optional[str]:
        """Extract chapter title from HTML.

        Override for site-specific title extraction.
        Default: find <h2> or parse <title> tag.
        """

    def get_next_chapter_url(self, html: str, current_url: str) -> Optional[str]:
        """Find the URL of the next chapter by parsing the HTML.

        Override for site-specific next-chapter detection.
        Default: look for links with "next chapter" text, then try URL incrementing.
        """

    def generate_next_url(self, current_url: str) -> Optional[str]:
        """Generate the next chapter URL from the current URL alone (no HTML).

        Override for site-specific URL pattern generation. This is used as a
        fallback when get_next_chapter_url() returns None (e.g. the HTML didn't
        contain a next link, or the page was fetched without full rendering).

        Examples:
            - URL incrementing: /chapter_5.html -> /chapter_6.html
            - Path-based: /novel/book/5 -> /novel/book/6
            - Query-based: ?ch=5 -> ?ch=6

        Default: tries common URL number incrementing patterns.
        """

    def get_novel_info(self, html: str, chapter_url: str) -> dict:
        """Extract novel metadata (title, index URL) from the chapter page.

        Override for site-specific metadata extraction.
        Default: parse breadcrumbs or <title> tag.

        Returns:
            dict with keys "title" (str|None) and "index_url" (str|None).
        """

    def get_content_selectors(self) -> list[str]:
        """CSS selectors for the main chapter content area.

        Override to provide site-specific selectors.
        The parser tries each selector in order and returns the first
        one that yields >200 chars of text.
        """

    def get_noise_selectors(self) -> list[str]:
        """Additional CSS selectors for elements to strip (ads, nav, etc.).

        Override to add site-specific noise removal.
        Default: standard noise (script, style, nav, footer, etc.).
        """
        from scraping.normalizer import DEFAULT_NOISE_SELECTORS
        return DEFAULT_NOISE_SELECTORS

    def get_trailing_cutoffs(self) -> list[str]:
        """Regex patterns for lines that mark end of chapter content.

        Override to add site-specific cutoff markers.
        Default: generic patterns like "user comments", "hot keywords".
        """
