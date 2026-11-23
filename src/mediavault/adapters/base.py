"""Adapter protocol shared by every platform backend.

An adapter turns a CanonicalURL into a normalized Resource (probe) and/or
downloads its media. Adapters declare which platforms they serve and a
match score so the registry can build ordered fallback chains.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from mediavault.config import Config
from mediavault.models import Resource
from mediavault.routing import CanonicalURL

Capability = Literal["video", "image_set", "no_watermark", "transcript"]


class AdapterError(Exception):
    """Hard failure for this URL (unsupported, private, deleted, ...)."""


class AdapterStale(AdapterError):
    """Platform changed its page/API shape: registry should try the next adapter."""


@dataclass
class Adapter(ABC):
    name: str
    config: Config
    platforms: frozenset[str] = frozenset()
    capabilities: frozenset[Capability] = frozenset()
    requires: frozenset[str] = frozenset()  # "proxy" / "cookies" / "ffmpeg"

    def supports(self, url: CanonicalURL) -> bool:
        return url.platform in self.platforms or url.platform == "unknown"

    def match_score(self, url: CanonicalURL) -> int:
        """Higher wins. Generic adapters return a low score for unknown hosts."""
        return 100 if url.platform in self.platforms else 10

    @abstractmethod
    async def probe(self, url: CanonicalURL) -> Resource:
        """Resolve metadata and direct media URLs without downloading bytes."""

    @abstractmethod
    async def download(self, resource: Resource, quality: str = "compat") -> list[str]:
        """Materialize media under MEDIAVAULT_HOME, return local file paths."""
