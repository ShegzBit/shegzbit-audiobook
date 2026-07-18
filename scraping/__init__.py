"""
scraping/__init__.py — Package init.
"""

from scraping.detector import ChallengeDetector, ChallengeType, detector
from scraping.fetcher import FetchResult, Fetcher
from scraping.normalizer import TextNormalizer, normalizer
from scraping.parser import HTMLParser, parser

__all__ = [
    "ChallengeDetector",
    "ChallengeType",
    "detector",
    "FetchResult",
    "Fetcher",
    "TextNormalizer",
    "normalizer",
    "HTMLParser",
    "parser",
]
