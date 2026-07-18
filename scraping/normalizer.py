"""
scraping/normalizer.py — Text cleanup and normalization.

Removes noise patterns, collapses whitespace, strips navigation/ad text,
and detects trailing cutoffs where chapter content ends.
"""

import re

# ── Noise patterns (matched against lowercased lines) ───────────────────────

DEFAULT_NOISE_PATTERNS = [
    r"advertisement",
    r"report chapter",
    r"next chapter",
    r"previous chapter",
    r"read more chapters?.*",
    r"bookmark this page",
    r"^\s*chapter list\s*$",
    r"please disable your ad.?blocker",
    r"if you.*enjoy.*support.*author",
    r"^default$",
    r"^dyslexic$",
    r"^roboto$",
    r"^lora$",
    r"^prev$",
    r"^next$",
    r"^index$",
    r"chevron_left",
    r"chevron_right",
    r"^nights_stay$",
    r"^home$",
    r"tap the screen to use advanced tools",
    r"you can use left and right keyboard keys",
    r"^search$",
    r"^categories$",
    r"^tags$",
    r"^updates$",
    r"your (fictional|fan-fiction) stories hub",
]

# ── Trailing cutoffs (lines matching these end the chapter) ──────────────────

DEFAULT_TRAILING_CUTOFFS = [
    r"you'?ll also like",
    r"^\s*###?\s*user comments",
    r"^\s*###?\s*hot keywords",
]

# ── Default noise element selectors ─────────────────────────────────────────

DEFAULT_NOISE_SELECTORS = [
    "script", "style", "nav", "header", "footer", "aside", "form",
    "iframe", "noscript", "button", "svg",
]


class TextNormalizer:
    """Cleans and normalizes extracted chapter text."""

    def __init__(
        self,
        extra_noise_patterns: list[str] | None = None,
        extra_trailing_cutoffs: list[str] | None = None,
    ):
        self.noise_patterns = DEFAULT_NOISE_PATTERNS + (extra_noise_patterns or [])
        self.trailing_cutoffs = DEFAULT_TRAILING_CUTOFFS + (extra_trailing_cutoffs or [])

    def normalize(self, raw: str) -> str:
        """Clean raw extracted text: strip noise, collapse whitespace."""
        lines = [ln.strip() for ln in raw.splitlines()]
        cleaned = []
        for ln in lines:
            if not ln:
                continue
            low = ln.lower()
            if any(re.search(pat, low) for pat in self.trailing_cutoffs):
                break
            if any(re.search(pat, low) for pat in self.noise_patterns):
                continue
            if len(ln) < 3:
                continue
            cleaned.append(ln)
        text = "\n\n".join(cleaned)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()

    def word_count(self, text: str) -> int:
        """Count words in normalized text."""
        return len(text.split())


normalizer = TextNormalizer()
