"""
scraping/parser.py — HTML parsing strategies.

Provides extraction methods that sources can compose or override:
- CSS selector extraction
- trafilatura article extraction
- BS4 heuristic (largest text block)
- Title detection
- Next-chapter URL detection
- Novel metadata extraction (breadcrumbs, title parsing)
"""

import logging
import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scraping.normalizer import DEFAULT_NOISE_SELECTORS

logger = logging.getLogger(__name__)

try:
    import trafilatura
except ImportError:
    trafilatura = None

# ── Default content selectors (used by GenericSource) ───────────────────────

DEFAULT_CONTENT_SELECTORS = [
    "#chapter-content",
    ".chapter-content",
    ".chapter-c",
    "#chr-content",
    ".chr-c",
    ".txt",
    "#content-area",
    "article .content",
    ".reading-content",
]


class HTMLParser:
    """Collection of HTML parsing strategies that sources can use."""

    def extract_with_selectors(
        self, html: str, selectors: list[str], noise_selectors: list[str] | None = None
    ) -> Optional[str]:
        """Try each CSS selector in order, return first result with >200 chars.

        Strips noise elements before extraction.
        """
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(noise_selectors or DEFAULT_NOISE_SELECTORS):
            tag.decompose()
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                paragraphs = node.find_all("p")
                text = (
                    "\n\n".join(p.get_text(" ", strip=True) for p in paragraphs)
                    if paragraphs
                    else node.get_text("\n\n", strip=True)
                )
                if len(text) > 200:
                    return text
        return None

    def extract_with_trafilatura(self, html: str) -> Optional[str]:
        """General article extraction via trafilatura."""
        if trafilatura is None:
            return None
        return trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
        )

    def extract_with_bs4_heuristic(self, html: str) -> Optional[str]:
        """Fallback extraction: find the largest text block via BS4."""
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(DEFAULT_NOISE_SELECTORS):
            tag.decompose()

        # Strategy 1: largest div/article/section
        candidates = soup.find_all(["div", "article", "section"])
        best, best_len = None, 0
        for c in candidates:
            t = c.get_text(" ", strip=True)
            if len(t) > best_len:
                best, best_len = c, len(t)
        if best and best_len > 200:
            paragraphs = best.find_all("p")
            if paragraphs:
                text = "\n\n".join(p.get_text(" ", strip=True) for p in paragraphs)
                if len(text) > 200:
                    return text
            text = best.get_text("\n\n", strip=True)
            if len(text) > 200:
                return text

        # Strategy 2: all <p> tags
        all_paragraphs = soup.find_all("p")
        if all_paragraphs:
            text = "\n\n".join(p.get_text(" ", strip=True) for p in all_paragraphs)
            if len(text) > 200:
                return text

        # Strategy 3: any element with substantial text
        for tag in soup.find_all(True):
            t = tag.get_text(" ", strip=True)
            if len(t) > 200 and tag.name not in ("html", "body", "head"):
                return t

        # Strategy 4: last resort
        text = soup.get_text(" ", strip=True)
        return text if len(text) > 200 else None

    def extract_title(self, html: str) -> Optional[str]:
        """Extract chapter title from <h2> or <title> tag."""
        soup = BeautifulSoup(html, "html.parser")
        h2 = soup.find("h2")
        if h2 and h2.get_text(strip=True):
            return h2.get_text(strip=True)
        title_tag = soup.find("title")
        if title_tag and title_tag.get_text(strip=True):
            raw = title_tag.get_text(strip=True)
            return raw.split("|")[0].split("-")[0].strip()
        return None

    def detect_next_chapter_url(self, html: str, current_url: str) -> Optional[str]:
        """Find next chapter link by text or URL pattern incrementing."""
        soup = BeautifulSoup(html, "html.parser")
        next_kws = ["next chapter", "next chap", "next \u2192", "\u2192", "next \u00bb", "\u00bb"]
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            if any(kw in text for kw in next_kws) and len(text) < 40:
                href = a["href"]
                if href and not href.startswith(("javascript:", "#", "mailto:")):
                    return urljoin(current_url, href)

        m = re.search(r"(_|-)(\d+)(\.html?)?(\?.*)?$", current_url)
        if m:
            prefix = m.group(1)
            num = int(m.group(2))
            suffix = m.group(3) or ""
            query = m.group(4) or ""
            next_url = re.sub(
                r"(_|-)(\d+)(\.html?)?(\?.*)?$",
                f"{prefix}{num + 1}{suffix}{query}",
                current_url,
            )
            if next_url != current_url:
                return next_url

        return None

    def detect_novel_info(self, html: str, chapter_url: str) -> dict:
        """Extract novel title + index URL from breadcrumbs or <title>."""
        soup = BeautifulSoup(html, "html.parser")

        for sel in [".breadcrumb a", ".breadcrumbs a", "nav .breadcrumb a", ".crumbs a"]:
            crumbs = soup.select(sel)
            if len(crumbs) >= 2:
                a = crumbs[-2]
                title = a.get_text(strip=True)
                href = a.get("href", "")
                if title and href:
                    return {"title": title, "index_url": urljoin(chapter_url, href)}

        title_tag = soup.find("title")
        if title_tag:
            raw = title_tag.get_text(strip=True)
            raw = re.split(r"\s*[|\-\u2013\u2014]\s*", raw)[0].strip()
            novel_title = re.sub(
                r"^chapter\s+\d+\s*[\u2013\u2014:-]\s*", "", raw, flags=re.IGNORECASE
            ).strip()
            novel_title = re.sub(
                r"\s+chapter\s+\d+.*$", "", novel_title, flags=re.IGNORECASE
            ).strip()
            if novel_title and len(novel_title) > 3:
                return {"title": novel_title, "index_url": None}

        return {"title": None, "index_url": None}


parser = HTMLParser()
