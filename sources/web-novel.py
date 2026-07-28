# sources/webnovel.py

from sources.base import ChapterText, NovelSource
from scraping.normalizer import normalizer
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import json
import re
from typing import Optional

class WebNovelSource(NovelSource):

    name = "webnovel"
    domain_patterns = ["webnovel.com", "*.webnovel.com"]
    requires_browser = False

    @classmethod
    def supports(cls, url: str) -> bool:
        return "webnovel.com" in url

    def extract(self, html: str, url: str) -> ChapterText:
        print("extract() called", flush=True)
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(self.get_noise_selectors()):
            tag.decompose()

        # Chapter content
        content = soup.select_one(".cha-content")
        if not content:
            raise ValueError("No chapter content found")

        paragraphs = content.find_all("p")
        text = "\n\n".join(p.get_text(" ", strip=True) for p in paragraphs)
        text = normalizer.normalize(text)

        # Title
        title = self.get_chapter_title(html) or "Untitled Chapter"

        # Next chapter link
        next_url = self.get_next_chapter_url(html, url)

        # Novel info
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

    def get_content_selectors(self) -> list[str]:
        return [".cha-content"]

    def get_chapter_title(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        h1 = soup.find("h1")
        return h1.get_text(strip=True) if h1 else None

    def get_next_chapter_url(self, html: str, current_url: str) -> Optional[str]:
        m = re.search(r"var\s+chapInfo\s*=\s*(\{.*?\});", html, re.DOTALL)
        print('get_next_chapter')
        if m:
            print (m.group(1))
            try:
                chap_info = json.loads(m.group(1))
                book_id = chap_info["bookInfo"]["bookId"]
                next_id = chap_info["chapterInfo"].get("nextChapterId")
                if next_id:
                    return f"https://www.webnovel.com/book/{book_id}/{next_id}"
            except Exception:
                pass
        return None

    def get_novel_info(self, html: str, chapter_url: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        crumbs = soup.select(".crumb a, .breadcrumb a")
        if len(crumbs) >= 2:
            return {
                "title": crumbs[-2].get_text(strip=True),
                "index_url": urljoin(chapter_url, crumbs[-2]["href"]),
            }
        return {"title": None, "index_url": None}

    def get_noise_selectors(self) -> list[str]:
        return [
            "script", "style", "nav", "footer",
            "i.icon", ".popup", ".ad-banner"
        ]

