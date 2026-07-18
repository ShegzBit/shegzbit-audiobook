"""
scraping/detector.py — Challenge page and HTTP error classification.

Detects Cloudflare, reCAPTCHA, DDoS-Guard, and other anti-bot pages.
Classifies HTTP errors into structured exception types.
"""

import logging
from enum import Enum
from typing import Optional

from errors import (
    CaptchaError,
    ForbiddenError,
    HttpError,
    NotFoundError,
    RateLimitError,
    ScrapingError,
)

logger = logging.getLogger(__name__)


class ChallengeType(Enum):
    CLOUDFLARE = "cloudflare"
    RECAPTCHA = "recaptcha"
    TURNSTILE = "turnstile"
    DDOS_GUARD = "ddos_guard"
    GENERIC = "generic"


# ── Challenge markers (lowercase) ───────────────────────────────────────────

CLOUDFLARE_MARKERS = [
    "cf-challenge",
    "cf-turnstile",
    "just a moment",
    "checking your browser",
    "checking if the site connection is secure",
    "enable javascript and cookies to continue",
    "please wait while we verify",
    "browser verification",
]

RECAPTCHA_MARKERS = [
    "g-recaptcha",
    "recaptcha",
    "please verify you are human",
    "verifies you are not a bot",
    "verifies you're not a bot",
]

DDOS_GUARD_MARKERS = [
    "ddos-guard",
    "ddosguard",
    "please wait while we are checking your browser",
]

GENERIC_MARKERS = [
    "security verification",
    "security service to protect",
    "access denied",
    "request rejected",
    "bot detection",
]

ALL_MARKERS: dict[ChallengeType, list[str]] = {
    ChallengeType.CLOUDFLARE: CLOUDFLARE_MARKERS,
    ChallengeType.RECAPTCHA: RECAPTCHA_MARKERS,
    ChallengeType.DDOS_GUARD: DDOS_GUARD_MARKERS,
    ChallengeType.GENERIC: GENERIC_MARKERS,
}


class ChallengeDetector:
    """Detects anti-bot challenge pages and classifies HTTP errors."""

    def is_challenge(self, html: str) -> Optional[ChallengeType]:
        """Check if the HTML is a challenge/captcha page.

        Returns the ChallengeType if detected, None if the page is clean.
        """
        lower = html.lower()
        for challenge_type, markers in ALL_MARKERS.items():
            for marker in markers:
                if marker in lower:
                    return challenge_type
        return None

    def is_captcha_page(self, html: str) -> bool:
        """Simple boolean check: is this a challenge page?"""
        return self.is_challenge(html) is not None

    def classify_http_error(self, status_code: int, url: str = "") -> ScrapingError:
        """Map an HTTP status code to a structured error.

        Returns:
            ScrapingError subclass with appropriate recoverable flag.
        """
        if status_code == 404:
            return NotFoundError(url=url)
        elif status_code == 403:
            return ForbiddenError(url=url)
        elif status_code == 429:
            return RateLimitError(url=url)
        elif status_code >= 400:
            return HttpError(status_code, url=url)
        return HttpError(status_code, url=url)


detector = ChallengeDetector()
