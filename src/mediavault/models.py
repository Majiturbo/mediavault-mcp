"""Platform-neutral data models.

Everything an adapter returns is mapped into these dataclasses so the MCP
surface never leaks a platform-specific JSON shape (yt-dlp info dicts,
f2 filters, future SSR parsers all normalize to Resource).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

ContentKind = Literal["video", "image_set", "mixed", "unknown"]
NoWatermark = Literal["native", "unknown", "none"]
MediaKind = Literal["video", "image", "audio"]


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Author:
    id: str | None = None
    name: str | None = None
    url: str | None = None


@dataclass
class MediaItem:
    """One downloadable piece of media inside a Resource."""

    kind: MediaKind
    url: str | None = None
    index: int = 0
    # native  = source stream has no burned-in platform watermark
    # unknown = adapter could not verify (never claim removal we did not do)
    # none    = source itself carries a watermark
    no_watermark: NoWatermark = "unknown"
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    ext: str | None = None
    codec: str | None = None
    size_hint: int | None = None
    local_path: str | None = None


@dataclass
class Resource:
    """Normalized result of probing one page/share link."""

    canonical_id: str
    platform: str
    content_type: ContentKind
    page_url: str
    title: str | None = None
    author: Author = field(default_factory=Author)
    caption: str | None = None
    items: list[MediaItem] = field(default_factory=list)
    adapter_used: str | None = None
    fallback_chain: list[str] = field(default_factory=list)
    # True when the chain had to degrade (stale adapter, partial data, ...)
    degraded: bool = False
    notes: list[str] = field(default_factory=list)
    fetched_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DownloadResult:
    resource: Resource
    files: list[str]
    output_dir: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource.to_dict(),
            "files": self.files,
            "output_dir": self.output_dir,
        }
