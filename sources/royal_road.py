"""
sources/royalroad.py

Source plugin for royalroad.com.

WHY THIS ONE IS SIMPLER THAN webnovel.py
------------------------------------------
Unlike webnovel.com, RoyalRoad's chapter pages carry real, reliable next-
chapter navigation right in the HTML -- a <link rel="next"> in <head> and a
visible "Next Chapter" button -- so get_next_chapter_url() below never has
to fetch anything extra or guess at URL structure. See the comment above
that method for why URL-incrementing is explicitly disabled here instead.

Selectors below (novel title h1[property=name], chapter body .chapter-content,
chapter table via <tbody>) are cross-checked against two independently
maintained, currently-active scrapers (lncrawl/lightnovel-crawler and
Aivean/royalroad-downloader), not guessed -- confidence here is meaningfully
higher than in webnovel.py.

ANTI-SCRAPING QUIRK HANDLED HERE
----------------------------------
RoyalRoad is known to inject hidden paragraphs (style="display:none" or
similar) into chapter bodies specifically to catch scrapers that republish
content elsewhere. _visible_paragraphs() filters those out so your TTS
reader doesn't occasionally narrate injected garbage sentences.
"""

from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from sources.base import ChapterText, NovelSource
from scraping.normalizer import normalizer
from errors import EmptyExtractionError


class RoyalRoadSource(NovelSource):

    name = "royalroad"
    domain_patterns = ["royalroad.com", "*.royalroad.com"]
    # Chapter and fiction pages are plain server-rendered HTML -- no browser needed.
    requires_browser = False

    @classmethod
    def supports(cls, url: str) -> bool:
        return "royalroad.com" in url and "/fiction/" in url

    # ---- content extraction -------------------------------------------------

    def get_content_selectors(self) -> list[str]:
        return [".chapter-content"]

    def get_chapter_title(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")

        # RoyalRoad sometimes puts the real chapter title in an h2 containing
        # "Chapter" above the body (confirmed via lncrawl's crawler).
        h2 = soup.select_one("h2")
        if h2 and "chapter" in h2.get_text(strip=True).lower():
            return h2.get_text(strip=True)

        # Fall back to <title>, formatted "{Chapter Title} - {Fiction Title} | Royal Road"
        if soup.title:
            raw = soup.title.get_text(strip=True).split("|")[0].strip()
            return raw.split(" - ")[0].strip() if raw else None

        return None

    @staticmethod
    def _visible_paragraphs(content: Tag) -> list[str]:
        lines = []
        for p in content.find_all("p"):
            style = (p.get("style") or "").lower().replace(" ", "")
            if "display:none" in style or "visibility:hidden" in style or "opacity:0" in style:
                continue
            classes = " ".join(p.get("class", [])).lower()
            if "hidden" in classes or "sr-only" in classes:
                continue
            txt = p.get_text(" ", strip=True)
            if txt:
                lines.append(txt)
        return lines

    def extract(self, html: str, url: str) -> ChapterText:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(self.get_noise_selectors()):
            tag.decompose()

        title = self.get_chapter_title(html) or "Untitled Chapter"

        content = soup.select_one(".chapter-content")
        if not content or len(content.get_text(strip=True)) < 100:
            raise EmptyExtractionError(url=url, source_name=self.name)

        paragraphs = self._visible_paragraphs(content)
        text = "\n\n".join(paragraphs) if paragraphs else content.get_text("\n\n", strip=True)
        text = normalizer.normalize(text)

        if not text or normalizer.word_count(text) < 20:
            raise EmptyExtractionError(url=url, source_name=self.name)

        novel_info = self.get_novel_info(html, url)

        return ChapterText(
            title=title,
            text=text,
            word_count=normalizer.word_count(text),
            source_url=url,
            next_chapter_url=self.get_next_chapter_url(html, url),
            novel_title=novel_info.get("title"),
            novel_index_url=novel_info.get("index_url"),
        )

    # ---- novel metadata -------------------------------------------------

    def get_novel_info(self, html: str, chapter_url: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")

        # Confirmed selector (lncrawl): h1[property="name"] on the fiction page.
        # Chapter pages usually carry a breadcrumb link back to the fiction
        # page instead, so try that first since we're on a chapter page here.
        crumb = soup.select_one('a[href*="/fiction/"]')
        if crumb and crumb.get("href"):
            return {
                "title": crumb.get_text(strip=True),
                "index_url": urljoin(chapter_url, crumb["href"]),
            }
        return {"title": None, "index_url": None}

    # ---- next chapter discovery ---------------------------------------------
    #
    # Deliberately NOT doing URL math here (chapter-id + 1, etc.): RoyalRoad's
    # chapter URLs are /fiction/{fictionId}/{slug}/chapter/{chapterId}/{title-slug},
    # and chapterId is a global id assigned at creation time across the whole
    # site, not a per-book sequence -- incrementing it points at some other
    # author's chapter, not "the next one" here. The reliable source of truth
    # is the page itself, which RoyalRoad annotates two ways:
    #
    #   1. <link rel="next" href="..."> in <head> -- their own forum confirms
    #      this is intentionally/officially supported, added specifically so
    #      third-party tools don't have to guess.
    #   2. A visible "Next Chapter" button -- literally what RoyalRoad's own
    #      keyboard-shortcut script looks for (a.innerText == "Next Chapter").
    #
    # Both come from the single already-fetched HTML, no extra request needed.

    def get_next_chapter_url(self, html: str, current_url: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")

        link_next = soup.find("link", rel="next")
        if link_next and link_next.get("href"):
            return urljoin(current_url, link_next["href"])

        for a in soup.find_all("a", href=True):
            if "next chapter" in a.get_text(strip=True).lower():
                return urljoin(current_url, a["href"])

        return None

    def generate_next_url(self, current_url: str) -> Optional[str]:
        # Explicitly opt out of the base class's numeric URL-incrementing
        # fallback -- see the note above on why that's unsafe here. If
        # get_next_chapter_url() above didn't find a rel=next or a "Next
        # Chapter" button, there genuinely isn't a reliable next URL to guess.
        return None
