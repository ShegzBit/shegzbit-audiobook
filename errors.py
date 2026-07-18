"""
errors.py — Structured error hierarchy for the scraping pipeline.

All scraping errors inherit from ScrapingError. Each error carries
enough context for retry decisions and user-facing messages.
"""


class ScrapingError(Exception):
    """Base class for all scraping errors."""

    def __init__(self, message: str, url: str = "", source_name: str = ""):
        self.url = url
        self.source_name = source_name
        self.recoverable = False
        super().__init__(message)


class CaptchaError(ScrapingError):
    """Challenge page detected (Cloudflare, reCAPTCHA, DDoS-Guard, etc.)."""

    def __init__(self, message: str = "Captcha/challenge page detected", **kwargs):
        super().__init__(message, **kwargs)
        self.recoverable = False


class HttpError(ScrapingError):
    """HTTP error response."""

    def __init__(self, status_code: int, message: str = "", **kwargs):
        self.status_code = status_code
        super().__init__(message or f"HTTP {status_code}", **kwargs)
        self.recoverable = status_code in (429, 500, 502, 503, 504)


class NotFoundError(HttpError):
    """HTTP 404 — chapter not found."""

    def __init__(self, message: str = "Page not found (HTTP 404)", **kwargs):
        super().__init__(404, message, **kwargs)
        self.recoverable = False


class ForbiddenError(HttpError):
    """HTTP 403 — access denied."""

    def __init__(self, message: str = "Access denied (HTTP 403)", **kwargs):
        super().__init__(403, message, **kwargs)
        self.recoverable = False


class RateLimitError(HttpError):
    """HTTP 429 — too many requests."""

    def __init__(self, message: str = "Rate limited (HTTP 429)", **kwargs):
        super().__init__(429, message, **kwargs)
        self.recoverable = True


class NetworkError(ScrapingError):
    """Connection failure, DNS error, timeout, etc."""

    def __init__(self, message: str = "Network error", **kwargs):
        super().__init__(message, **kwargs)
        self.recoverable = True


class EmptyExtractionError(ScrapingError):
    """Extraction produced no usable text."""

    def __init__(self, message: str = "No chapter text found on page", **kwargs):
        super().__init__(message, **kwargs)
        self.recoverable = False


class ParsingError(ScrapingError):
    """HTML structure is unexpected or unparseable."""

    def __init__(self, message: str = "Failed to parse page HTML", **kwargs):
        super().__init__(message, **kwargs)
        self.recoverable = False
