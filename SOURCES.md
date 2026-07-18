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

Find the URL of the next chapter by parsing the HTML.

**Default:** Look for links with "next chapter" text, then try URL number incrementing (`chapter_5.html` -> `chapter_6.html`).

```python
def get_next_chapter_url(self, html: str, current_url: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one("a.next-btn")
    return urljoin(current_url, a["href"]) if a else None
```

### `generate_next_url(current_url: str) -> Optional[str]`

Generate the next chapter URL from the current URL alone (no HTML needed). Used as a **fallback** when `get_next_chapter_url()` returns `None` — for example, when the HTML didn't contain a next link, or the page was fetched without full rendering.

**Default:** Tries common URL incrementing patterns:
- `/chapter_5.html` -> `/chapter_6.html`
- `/novel/book-12` -> `/novel/book-13`
- `/read/42` -> `/read/43`
- `?ch=5` -> `?ch=6`

You can also use the `increment_url` utility for generic incrementing:

```python
from scraping.parser import increment_url

def generate_next_url(self, current_url: str) -> Optional[str]:
    # Royal Road: /fiction/12345/chapter/67890/slug -> increment chapter ID
    m = re.search(r"/chapter/(\d+)/", current_url)
    if m:
        old_id = int(m.group(1))
        new_id = old_id + 1
        next_url = current_url.replace(f"/chapter/{old_id}/", f"/chapter/{new_id}/")
        if next_url != current_url:
            return next_url
    # Fallback to generic incrementing
    return increment_url(current_url)
```

**Pipeline behavior:** The extractor calls `get_next_chapter_url(html, url)` first. If that returns `None`, it calls `generate_next_url(url)` as fallback. This ensures the auto-queue feature works even when the HTML-based detection fails.

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
| `from scraping.parser import increment_url` | `increment_url(url)` — increment a number in the URL path/query |
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
