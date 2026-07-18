# Adding a New Novel Source

This document describes how to add support for a new novel website.

## Quick Start

1. Create `sources/my_site.py`
2. Implement the `NovelSource` interface
3. The registry auto-discovers it — no other files need to change

```python
# sources/my_site.py

from sources.base import ChapterText, NovelSource
from scraping.normalizer import normalizer
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from typing import Optional


class MySiteSource(NovelSource):

    name = "my_site"
    domain_patterns = ["mysite.com", "*.mysite.com"]
    requires_browser = False

    @classmethod
    def supports(cls, url: str) -> bool:
        return "mysite.com" in url

    def extract(self, html: str, url: str) -> ChapterText:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(self.get_noise_selectors()):
            tag.decompose()

        content = soup.select_one(".chapter-body")
        paragraphs = content.find_all("p")
        text = "\n\n".join(p.get_text(" ", strip=True) for p in paragraphs)
        text = normalizer.normalize(text)

        title = soup.select_one("h1").get_text(strip=True)
        next_a = soup.select_one("a.next-chapter")
        next_url = urljoin(url, next_a["href"]) if next_a else None

        return ChapterText(
            title=title,
            text=text,
            word_count=normalizer.word_count(text),
            source_url=url,
            next_chapter_url=next_url,
        )
```

## Required Attributes

| Attribute | Type | Description |
|---|---|---|
| `name` | `str` | Unique identifier (e.g. `"wuxia_world"`, `"royalroad"`) |
| `domain_patterns` | `list[str]` | Domains to match against. Use exact (`"wuxiaworld.com"`) or glob (`"*.wuxiaworld.com"`) |
| `requires_browser` | `bool` | `True` = always use Playwright. `False` = try HTTP first, fall back to browser on captcha |

## Required Methods

### `supports(url: str) -> bool`

Class method. Return `True` if this source handles the given URL. The registry calls sources in order — first match wins.

```python
@classmethod
def supports(cls, url: str) -> bool:
    return "mysite.com" in url
```

### `extract(html: str, url: str) -> ChapterText`

Parse the HTML and return chapter content. This method receives **already-fetched** HTML — it does not fetch.

Must return a `ChapterText` with at minimum `title`, `text`, `word_count`, and `source_url`.

```python
def extract(self, html: str, url: str) -> ChapterText:
    # ... parse html ...
    return ChapterText(
        title="Chapter 1",
        text="The cleaned chapter text...",
        word_count=1234,
        source_url=url,
        next_chapter_url="https://...",
        novel_title="My Novel",
        novel_index_url="https://...",
    )
```

## Optional Overrides

These methods have sensible defaults. Override them for site-specific behavior.

### `get_content_selectors() -> list[str]`

CSS selectors for the main chapter content area. Tried in order; first match with >200 chars wins.

**Default:** 9 common selectors including `#chapter-content`, `.chapter-content`, `.chapter-c`, `.reading-content`.

```python
def get_content_selectors(self) -> list[str]:
    return [".story-content", "#chapter-text"]
```

### `get_chapter_title(html: str) -> Optional[str]`

Extract chapter title from HTML.

**Default:** Find `<h2>`, then fall back to parsing `<title>` tag.

```python
def get_chapter_title(self, html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    return h1.get_text(strip=True) if h1 else None
```

### `get_next_chapter_url(html: str, current_url: str) -> Optional[str]`

Find the URL of the next chapter.

**Default:** Look for links with "next chapter" text, then try URL number incrementing (`chapter_5.html` -> `chapter_6.html`).

```python
def get_next_chapter_url(self, html: str, current_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one("a.next-btn")
    return urljoin(current_url, a["href"]) if a else None
```

### `get_novel_info(html: str, chapter_url: str) -> dict`

Extract novel metadata from the chapter page.

**Default:** Parse breadcrumb navigation or `<title>` tag.

**Returns:** `{"title": str | None, "index_url": str | None}`

```python
def get_novel_info(self, html: str, chapter_url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    crumbs = soup.select(".breadcrumb a")
    if len(crumbs) >= 2:
        return {
            "title": crumbs[-2].get_text(strip=True),
            "index_url": urljoin(chapter_url, crumbs[-2]["href"]),
        }
    return {"title": None, "index_url": None}
```

### `get_noise_selectors() -> list[str]`

Additional CSS selectors for elements to strip before extraction (ads, navigation, sidebars).

**Default:** `script`, `style`, `nav`, `header`, `footer`, `aside`, `form`, `iframe`, `noscript`, `button`, `svg`.

```python
def get_noise_selectors(self) -> list[str]:
    return [
        "script", "style", "nav", "footer",
        ".sidebar", ".ad-banner", ".popup",
    ]
```

### `get_trailing_cutoffs() -> list[str]`

Regex patterns for lines that mark the end of chapter content. When matched, everything after is discarded.

**Default:** `"you'll also like"`, `"user comments"`, `"hot keywords"`.

```python
def get_trailing_cutoffs(self) -> list[str]:
    return [r"next chapter preview", r"author's note"]
```

## ChapterText Dataclass

```python
@dataclass
class ChapterText:
    title: str                              # Required
    text: str                               # Required — clean chapter text
    word_count: int                         # Required
    source_url: str                         # Required
    next_chapter_url: Optional[str] = None  # For auto-queuing next chapter
    novel_title: Optional[str] = None       # For novel/library tracking
    novel_index_url: Optional[str] = None   # For novel/library tracking
```

## Available Utilities

Import these from the `scraping` and `sources` packages:

| Import | Usage |
|---|---|
| `from scraping.normalizer import normalizer` | `normalizer.normalize(text)` — strip noise, collapse whitespace |
| `from scraping.normalizer import normalizer` | `normalizer.word_count(text)` — accurate word count |
| `from scraping.parser import parser` | `parser.extract_with_trafilatura(html)` — general article extraction |
| `from scraping.parser import parser` | `parser.extract_with_bs4_heuristic(html)` — largest text block |
| `from scraping.detector import detector` | `detector.is_captcha_page(html)` — check for challenge pages |
| `from bs4 import BeautifulSoup` | HTML parsing (already a dependency) |

## Source Registration

Sources are auto-discovered at import time. The registry:

1. Scans all `.py` files in `sources/`
2. Imports each module
3. Finds all `NovelSource` subclasses
4. Sorts them: specific sources first, `generic` last

URL resolution tries each source's `supports()` in order. First match wins. If no source matches, `GenericSource` handles it with multi-strategy extraction.

## Example Sources

See `sources/wuxia_world.py` and `sources/royalroad.py` for complete implementations.

## Error Handling

If your extraction fails, raise structured errors from `errors.py`:

```python
from errors import EmptyExtractionError, CaptchaError, ParsingError

def extract(self, html, url):
    if not html:
        raise EmptyExtractionError(url=url, source_name=self.name)
    # ...
```

The worker handles retries automatically for recoverable errors (`NetworkError`, `RateLimitError`).

## Testing Your Source

```python
from sources import registry
from extractor import extract_chapter

# Verify resolution
source = registry.resolve("https://mysite.com/novel/chapter-1")
assert source.name == "my_site"

# Test extraction with saved HTML
with open("tests/fixtures/mysite_chapter.html") as f:
    html = f.read()
chapter = extract_chapter("https://mysite.com/novel/chapter-1", html)
assert chapter.word_count > 200
```
