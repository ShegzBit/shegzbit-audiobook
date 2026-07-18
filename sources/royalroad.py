"""
sources/royalroad.py — Example source for Royal Road.

Royal Road uses JavaScript rendering for some content, so this source
declares requires_browser = True. It also demonstrates a site where
the content selectors are well-known.
"""

import re
from typing import Optional

from bs4 import BeautifulSoup
from urllib.parse import urljoin

from sources.base import ChapterText, NovelSource
from scraping.normalizer import normalizer


class RoyalRoadSource(NovelSource):
    """Source implementation for royalroad.com."""

    name = "royal_road"
    domain_patterns = ["royalroad.com", "*.royalroad.com"]
    requires_browser = False

    @classmethod
    def supports(cls, url: str) -> bool:
        return "royalroad.com" in url

    def get_content_selectors(self) -> list[str]:
        return [
            ".chapter-content",
            ".portable-chapter-content",
        ]

    def get_noise_selectors(self) -> list[str]:
        return [
            "script", "style", "nav", "header", "footer", "aside",
            "iframe", "noscript", "button", "svg",
            ".ad", ".advertisement", ".chapter-nav", ".ratings",
        ]

    def get_chapter_title(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        # Royal Road chapter titles are in <h1> with class "font-white"
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            return h1.get_text(strip=True)
        return None

    def get_next_chapter_url(self, html: str, current_url: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        # Royal Road has explicit next chapter buttons
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            if text in ("next", "next chapter", "next \u2192"):
                return urljoin(current_url, a["href"])
            # Also check for aria-label or title attributes
            title = (a.get("title") or a.get("aria-label") or "").lower()
            if "next chapter" in title:
                return urljoin(current_url, a["href"])
        return None

    def get_novel_info(self, html: str, chapter_url: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        # Royal Road breadcrumb: Fiction Name > Chapter X > ...
        breadcrumbs = soup.select(".breadcrumb a, .breadcrumbs a")
        if len(breadcrumbs) >= 2:
            title = breadcrumbs[-2].get_text(strip=True)
            href = breadcrumbs[-2].get("href", "")
            if title:
                return {"title": title, "index_url": urljoin(chapter_url, href)}
        # Fallback: page title
        title_tag = soup.find("title")
        if title_tag:
            raw = title_tag.get_text(strip=True)
            parts = re.split(r"\s*[|\u2013\u2014]\s*", raw)
            if parts:
                return {"title": parts[0].strip(), "index_url": None}
        return {"title": None, "index_url": None}

    def extract(self, html: str, url: str) -> ChapterText:
        soup = BeautifulSoup(html, "html.parser")

        # Strip noise
        for tag in soup(self.get_noise_selectors()):
            tag.decompose()

        # Extract content
        text = ""
        for selector in self.get_content_selectors():
            node = soup.select_one(selector)
            if node:
                paragraphs = node.find_all("p")
                if paragraphs:
                    text = "\n\n".join(p.get_text(" ", strip=True) for p in paragraphs)
                else:
                    text = node.get_text("\n\n", strip=True)
                if len(text) > 200:
                    break

        text = normalizer.normalize(text)
        title = self.get_chapter_title(html) or "Untitled Chapter"
        next_url = self.get_next_chapter_url(html, url)
        novel_info = self.get_novel_info(html, url)

        return ChapterText(
            title=title,
            text=text,
            word_count=normalizer.word_count(text),
            source_url=url,
            next_chapter_url=next_url,
            novel_title=novel_info.get("title"),
            novel_index_url=novel_info.get("index_url"),
        )
