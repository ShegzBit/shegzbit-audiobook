"""
extractor.py — Chapter text extraction from web novel URLs.
Provides extract_chapter() as the single entry point.
"""

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    import trafilatura
except ImportError:
    trafilatura = None

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

NOISE_SELECTORS = [
    "script", "style", "nav", "header", "footer", "aside", "form",
    "iframe", "noscript", "button", "svg",
]

NOISE_PATTERNS = [
    r"advertisement", r"report chapter", r"next chapter", r"previous chapter",
    r"read more chapters?.*", r"bookmark this page", r"^\s*chapter list\s*$",
    r"please disable your ad.?blocker", r"if you.*enjoy.*support.*author",
    r"^default$", r"^dyslexic$", r"^roboto$", r"^lora$",
    r"^prev$", r"^next$", r"^index$", r"chevron_left", r"chevron_right",
    r"^nights_stay$", r"^home$",
    r"tap the screen to use advanced tools",
    r"you can use left and right keyboard keys",
    r"^search$", r"^categories$", r"^tags$", r"^updates$",
    r"your (fictional|fan-fiction) stories hub",
]

TRAILING_CUTOFFS = [
    r"you'?ll also like",
    r"^\s*###?\s*user comments",
    r"^\s*###?\s*hot keywords",
]

SITE_TEMPLATE_SELECTORS = [
    "#chapter-content", ".chapter-content", ".chapter-c",
    "#chr-content", ".chr-c", ".txt", "#content-area",
    "article .content", ".reading-content",
]

CAPTCHA_MARKERS = [
    "cf-challenge", "g-recaptcha", "cf-turnstile",
    "just a moment", "checking your browser", "ddos-guard",
    "please verify you are human", "enable javascript and cookies",
]


class CaptchaError(Exception):
    pass


@dataclass
class ChapterText:
    title: str
    text: str
    word_count: int
    source_url: str
    next_chapter_url: Optional[str] = None
    novel_title: Optional[str] = None
    novel_index_url: Optional[str] = None


def fetch_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def is_captcha_page(html: str) -> bool:
    lower = html.lower()
    return any(marker in lower for marker in CAPTCHA_MARKERS)


def extract_with_known_selectors(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(NOISE_SELECTORS):
        tag.decompose()
    for selector in SITE_TEMPLATE_SELECTORS:
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


def extract_with_trafilatura(html: str) -> Optional[str]:
    if trafilatura is None:
        return None
    return trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )


def extract_with_bs4_heuristic(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(NOISE_SELECTORS):
        tag.decompose()
    candidates = soup.find_all(["div", "article", "section"])
    best, best_len = None, 0
    for c in candidates:
        t = c.get_text(" ", strip=True)
        if len(t) > best_len:
            best, best_len = c, len(t)
    if best is None:
        return soup.get_text(" ", strip=True)
    paragraphs = best.find_all("p")
    if paragraphs:
        return "\n\n".join(p.get_text(" ", strip=True) for p in paragraphs)
    return best.get_text("\n\n", strip=True)


def clean_text(raw: str) -> str:
    lines = [ln.strip() for ln in raw.splitlines()]
    cleaned = []
    for ln in lines:
        if not ln:
            continue
        low = ln.lower()
        if any(re.search(pat, low) for pat in TRAILING_CUTOFFS):
            break
        if any(re.search(pat, low) for pat in NOISE_PATTERNS):
            continue
        if len(ln) < 3:
            continue
        cleaned.append(ln)
    text = "\n\n".join(cleaned)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def extract_chapter_title(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    h2 = soup.find("h2")
    if h2 and h2.get_text(strip=True):
        return h2.get_text(strip=True)
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        raw = title_tag.get_text(strip=True)
        return raw.split("|")[0].split("-")[0].strip()
    return None


def detect_next_chapter_url(html: str, current_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    next_kws = ["next chapter", "next chap", "next →", "→", "next »", "»"]
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


def detect_novel_info(html: str, chapter_url: str) -> dict:
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
        raw = re.split(r"\s*[|\-–—]\s*", raw)[0].strip()
        novel_title = re.sub(r"^chapter\s+\d+\s*[-–—:]\s*", "", raw, flags=re.IGNORECASE).strip()
        novel_title = re.sub(r"\s+chapter\s+\d+.*$", "", novel_title, flags=re.IGNORECASE).strip()
        if novel_title and len(novel_title) > 3:
            return {"title": novel_title, "index_url": None}

    return {"title": None, "index_url": None}


def extract_chapter(url: str, html: Optional[str] = None) -> ChapterText:
    """Main entry point. Fetch (if needed), extract, clean and return ChapterText."""
    if html is None:
        html = fetch_html(url)

    if is_captcha_page(html):
        raise CaptchaError(f"Captcha/challenge page detected at {url}")

    title = extract_chapter_title(html)
    next_url = detect_next_chapter_url(html, url)
    novel_info = detect_novel_info(html, url)

    text = extract_with_known_selectors(html)
    if not text:
        text = extract_with_trafilatura(html)
    if not text or len(text) < 200:
        text = extract_with_bs4_heuristic(html)

    text = clean_text(text or "")

    if len(text) < 100:
        raise ValueError(
            "Could not find substantial chapter text on this page. "
            f"Tried all extraction strategies on: {url}"
        )

    return ChapterText(
        title=title or "Untitled Chapter",
        text=text,
        word_count=len(text.split()),
        source_url=url,
        next_chapter_url=next_url,
        novel_title=novel_info.get("title"),
        novel_index_url=novel_info.get("index_url"),
    )
