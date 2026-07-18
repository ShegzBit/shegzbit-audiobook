"""
sources/__init__.py — Auto-discovery registry for novel sources.

Discovers all NovelSource subclasses in the sources/ directory at import time.
To add a new source, just create a .py file in sources/ with a NovelSource subclass.
"""

import importlib
import logging
import pkgutil
from typing import Optional
from fnmatch import fnmatch

from sources.base import NovelSource

logger = logging.getLogger(__name__)

_registry: list[NovelSource] = []
_discovered = False


def _discover():
    """Scan the sources/ package for NovelSource subclasses."""
    global _discovered, _registry
    if _discovered:
        return

    import sources as pkg

    for importer, module_name, is_pkg in pkgutil.iter_modules(pkg.__path__):
        if module_name in ("base", "__init__"):
            continue
        try:
            importlib.import_module(f"sources.{module_name}")
        except Exception:
            logger.exception("Failed to import source module: sources.%s", module_name)

    # Collect all concrete NovelSource subclasses (exclude base)
    for subclass in _iter_subclasses(NovelSource):
        if subclass is NovelSource:
            continue
        try:
            instance = subclass()
            _registry.append(instance)
            logger.info("Registered source: %s (domains: %s)", instance.name, instance.domain_patterns)
        except Exception:
            logger.exception("Failed to instantiate source: %s", subclass.__name__)

    # Sort: non-generic sources first, generic last
    _registry.sort(key=lambda s: (s.name == "generic", s.name))
    _discovered = True


def _iter_subclasses(cls):
    """Recursively find all subclasses of cls."""
    result = []
    for subclass in cls.__subclasses__():
        result.append(subclass)
        result.extend(_iter_subclasses(subclass))
    return result


class SourceRegistry:
    """Registry of available novel sources.

    Usage:
        from sources import registry
        source = registry.resolve("https://wuxiaworld.com/novel/some-book/chapter-1")
        chapter = source.extract(html, url)
    """

    def resolve(self, url: str) -> NovelSource:
        """Find the first source that supports this URL.

        Returns GenericSource as the fallback if no specific source matches.
        """
        _discover()
        for source in _registry:
            try:
                if source.supports(url):
                    return source
            except Exception:
                logger.exception("Error checking source %s for %s", source.name, url)
        # Fallback to generic (should always be present)
        return self._get_generic()

    def resolve_by_name(self, name: str) -> Optional[NovelSource]:
        """Find a source by its name attribute."""
        _discover()
        for source in _registry:
            if source.name == name:
                return source
        return None

    def all_sources(self) -> list[NovelSource]:
        """Return all registered sources."""
        _discover()
        return list(_registry)

    def _get_generic(self) -> NovelSource:
        """Get the generic fallback source."""
        for source in _registry:
            if source.name == "generic":
                return source
        raise RuntimeError("No generic source registered — sources/generic.py is missing")


registry = SourceRegistry()
