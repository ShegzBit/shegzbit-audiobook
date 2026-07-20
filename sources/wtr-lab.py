from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scraping.normalizer import normalizer
from sources.base import ChapterText, NovelSource


class WTRLabSource(NovelSource):
    name = "wtr_lab"

    domain_patterns = [
        "wtr-lab.com",
        "*.wtr-lab.com",
    ]

    requires_browser = False

    @classmethod
    def supports(cls, url: str) -> bool:
        return "wtr-lab.com" in url

    def extract(self, html: str, url: str) -> ChapterText:
        soup = BeautifulSoup(html, "html.parser")

        chapter = soup.select_one(".chapter-body")
        if chapter is None:
            raise ValueError("Unable to locate chapter body.")

        lines = []

        for line in chapter.select(".wtr-line"):
            text = line.get_text(" ", strip=True)

            if text:
                lines.append(text)

        text = "\n\n".join(lines)
        text = normalizer.normalize(text)

        title = self.get_chapter_title(html) or "Unknown Chapter"

        novel = self.get_novel_info(html, url)

        return ChapterText(
            title=title,
            text=text,
            word_count=normalizer.word_count(text),
            source_url=url,
            next_chapter_url=self.get_next_chapter_url(html, url),
            novel_title=novel["title"],
            novel_index_url=novel["index_url"],
        )

    def get_chapter_title(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")

        title = soup.select_one(".chapter-container > span.text-2xl")

        if title:
            return title.get_text(" ", strip=True)

        return None

    def get_next_chapter_url(
        self,
        html: str,
        current_url: str,
    ) -> Optional[str]:

        soup = BeautifulSoup(html, "html.parser")

        # WTR-Lab usually has navigation buttons near the bottom.
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True).lower()

            if "next" in text:
                return urljoin(current_url, a["href"])

        return self.generate_next_url(current_url)

    def get_novel_info(self, html: str, chapter_url: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")

        # If breadcrumb exists
        crumbs = soup.select("nav a,.breadcrumb a")

        if len(crumbs) >= 2:
            novel = crumbs[-2]

            return {
                "title": novel.get_text(strip=True),
                "index_url": urljoin(chapter_url, novel["href"]),
            }

        return {
            "title": None,
            "index_url": None,
        }
