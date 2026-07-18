"""
sources/wuxia_world.py — Example source for WuxiaWorld sites.

Demonstrates how to implement a site-specific source with custom selectors,
title extraction, and next-chapter detection.
"""

import re
from typing import Optional
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

from bs4 import BeautifulSoup

from sources.base import ChapterText, NovelSource
from scraping.normalizer import normalizer
from scraping.parser import increment_url


class WuxiaWorldSource(NovelSource):
    """Source implementation for wuxiaworld.com and its subdomains."""

    name = "wuxia_world"
    domain_patterns = ["wuxiaworld.com", "*.wuxiaworld.com"]
    requires_browser = False

    @classmethod
    def supports(cls, url: str) -> bool:
        return "wuxiaworld.com" in url

    def get_content_selectors(self) -> list[str]:
        return [
            "#chapter-content",
            ".chapter-content",
            ".content-inner",
        ]

    def get_noise_selectors(self) -> list[str]:
        return [
            "script", "style", "nav", "header", "footer", "aside",
            "iframe", "noscript", "button", "svg",
            ".sidebar", ".ad", ".advertisement", ".nav-chapter",
        ]

    def get_chapter_title(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        # WuxiaWorld typically puts title in an <h4> or <h3>
        for tag in ["h4", "h3", "h2"]:
            heading = soup.find(tag)
            if heading and heading.get_text(strip=True):
                return heading.get_text(strip=True)
        return None

    def get_next_chapter_url(self, html: str, current_url: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        # Look for the next chapter link in the chapter nav
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            if "next" in text and len(text) < 30:
                return urljoin(current_url, a["href"])
        return None

    def generate_next_url(self, current_url: str) -> Optional[str]:
        # WuxiaWorld uses /novel/book/chapter-N or /novel/book/chapter_N
        return increment_url(current_url)

    def get_novel_info(self, html: str, chapter_url: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        # Breadcrumb navigation typically has: Home > Novel Title > Chapter
        crumbs = soup.select(".breadcrumb a, .breadcrumbs a")
        if len(crumbs) >= 2:
            title = crumbs[-2].get_text(strip=True)
            href = crumbs[-2].get("href", "")
            if title:
                return {"title": title, "index_url": urljoin(chapter_url, href)}
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
