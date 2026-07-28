# sources/freewebnovel.py

from sources.base import ChapterText, NovelSource
from scraping.normalizer import normalizer
from scraping.parser import increment_url
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from typing import Optional
import re


class FreeWebNovelSource(NovelSource):

    name = "freewebnovel"
    domain_patterns = ["freewebnovel.com"]
    requires_browser = False

    @classmethod
    def supports(cls, url: str) -> bool:
        return "freewebnovel.com" in url

    def get_content_selectors(self) -> list[str]:
        return ["#article", "div.txt"]

    def get_chapter_title(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        article = soup.select_one("#article")
        if article:
            h4 = article.find("h4")
            if h4:
                return h4.get_text(strip=True)
        meta = soup.find("meta", {"property": "og:novel:chapter_name"})
        if meta and meta.get("content"):
            return meta["content"]
        return None

    def get_next_chapter_url(self, html: str, current_url: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        a = soup.find("a", id="next_url")
        if a and a.get("href"):
            return urljoin(current_url, a["href"])
        return None

    def generate_next_url(self, current_url: str) -> Optional[str]:
        return increment_url(current_url)

    def get_novel_info(self, html: str, chapter_url: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        name_meta = soup.find("meta", {"property": "og:novel:novel_name"})
        url_meta = soup.find("meta", {"property": "og:novel:index_url"})
        title = name_meta["content"] if name_meta and name_meta.get("content") else None
        index_url = url_meta["content"] if url_meta and url_meta.get("content") else None
        if not title:
            crumbs = soup.select(".cur a, .breadcrumb a")
            if len(crumbs) >= 2:
                title = crumbs[-2].get_text(strip=True)
                href = crumbs[-2].get("href")
                if href and not index_url:
                    index_url = urljoin(chapter_url, href)
        return {"title": title, "index_url": index_url}

    def get_noise_selectors(self) -> list[str]:
        return [
            "script", "style", "nav", "header", "footer", "aside",
            "iframe", "noscript", "button", "svg",
            ".reader-ad-skip", ".slot-frame", ".chapter-start", ".chapter-end",
            ".top", ".bottom", ".setting-box",
        ]

    def get_trailing_cutoffs(self) -> list[str]:
        return [r"you.ll also like", r"readers also liked"]

    def extract(self, html: str, url: str) -> ChapterText:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(self.get_noise_selectors()):
            tag.decompose()

        text = ""
        for selector in self.get_content_selectors():
            node = soup.select_one(selector)
            if node:
                paragraphs = node.find_all("p")
                if paragraphs:
                    text = "\n\n".join(
                        p.get_text(" ", strip=True) for p in paragraphs
                    )
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
