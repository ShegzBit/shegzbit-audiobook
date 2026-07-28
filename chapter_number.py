"""
chapter_number.py — Extract chapter number from title, HTML, or URL.

Used to generate systematic chapter_{NO} filenames for downloaded audio.
"""

import re
from typing import Optional
from urllib.parse import urlparse, parse_qs

# Title patterns: "Chapter 5", "Ch. 5", "Ch 5", leading digits like "005 ..."
_TITLE_PATTERNS = [
    re.compile(r"(?:chapter|ch\.?)\s*(\d+)", re.IGNORECASE),
    re.compile(r"^(\d+)[\s._\-:]+"),
]


def _from_title(title: str) -> Optional[int]:
    """Extract chapter number from the chapter title string."""
    for pat in _TITLE_PATTERNS:
        m = pat.search(title)
        if m:
            return int(m.group(1))
    return None


# HTML patterns: search specific elements for chapter number references
_HEADING_TAGS = ["h1", "h2", "h3"]
_HTML_PATTERNS = [
    re.compile(r"(?:chapter|ch\.?)\s*(\d+)", re.IGNORECASE),
    re.compile(r"(?:^|\s)(\d+)(?:\s*[:\-\u2013\u2014.]|\s*$)"),
]


def _from_html(html: str) -> Optional[int]:
    """Search HTML elements for a chapter number."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # 1. Check headings
    for tag_name in _HEADING_TAGS:
        heading = soup.find(tag_name)
        if heading:
            text = heading.get_text(strip=True)
            for pat in _HTML_PATTERNS:
                m = pat.search(text)
                if m:
                    return int(m.group(1))

    # 2. Check <title> tag
    title_tag = soup.find("title")
    if title_tag:
        raw = title_tag.get_text(strip=True)
        for pat in _HTML_PATTERNS:
            m = pat.search(raw)
            if m:
                return int(m.group(1))

    # 3. Check breadcrumb / nav links
    for sel in [".breadcrumb a", ".breadcrumbs a", "nav a", ".crumbs a"]:
        for a in soup.select(sel):
            text = a.get_text(strip=True)
            for pat in _HTML_PATTERNS:
                m = pat.search(text)
                if m:
                    return int(m.group(1))

    # 4. Check meta tags
    for meta in soup.find_all("meta"):
        content = meta.get("content", "")
        if content:
            for pat in _HTML_PATTERNS:
                m = pat.search(content)
                if m:
                    return int(m.group(1))

    return None


# URL patterns
_URL_PATTERNS = [
    re.compile(r"(?:chapter|ch)[/_-](\d+)", re.IGNORECASE),
    re.compile(r"/(\d+)/?$"),
    re.compile(r"[?&](?:ch|chapter|id|p)=(\d+)", re.IGNORECASE),
]


def _from_url(url: str) -> Optional[int]:
    """Extract chapter number from URL path or query parameters."""
    for pat in _URL_PATTERNS:
        m = pat.search(url)
        if m:
            return int(m.group(1))
    return None


def extract_chapter_number(
    title: str,
    html: Optional[str] = None,
    url: Optional[str] = None,
) -> Optional[int]:
    """Extract chapter number from available sources.

    Tries in order:
    1. Title (e.g. "Chapter 5: The Battle", "005 Prologue")
    2. HTML elements (headings, title tag, breadcrumbs, meta tags)
    3. URL patterns (path segments, query params)

    Returns None if no chapter number can be determined.
    """
    num = _from_title(title)
    if num is not None:
        return num

    if html:
        num = _from_html(html)
        if num is not None:
            return num

    if url:
        num = _from_url(url)
        if num is not None:
            return num

    return None
