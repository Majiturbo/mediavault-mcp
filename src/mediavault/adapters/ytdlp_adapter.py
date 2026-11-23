"""yt-dlp backed adapter: the generic workhorse for 1800+ sites.

yt-dlp ships as a library but its API is synchronous and blocking; we run it
in a worker thread so the async MCP server stays responsive. No-watermark
status is reported honestly: yt-dlp returns the platform's own source stream,
which carries no burned-in watermark on most platforms, but we cannot prove
it per-URL, so the default marking is "unknown" rather than a false "native".
"""

from __future__ import annotations

import asyncio
import functools
import json
import re
from pathlib import Path
from typing import Any

from mediavault.adapters.base import Adapter, AdapterError, AdapterStale
from mediavault.models import Author, MediaItem, Resource
from mediavault.routing import CanonicalURL

# yt-dlp extractor names we know deliver clean source streams.
_NATIVE_CLEAN_EXTRACTORS = {
    "twitter", "x", "tiktok", "youtube", "vimeo", "reddit", "twitch",
}

_QUALITY_FORMAT = {
    # best available, container/codec be damned
    "quality": "bestvideo+bestaudio/best",
    # small and fast; separate DASH streams are merged by ffmpeg
    "speed": "worstvideo+worstaudio/worst",
    # default: 1080p H.264 MP4 that opens in any player / editor
    "compat": (
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
        "/best[height<=1080][ext=mp4]/best[height<=1080]/best"
    ),
}


def _slug(value: str, limit: int = 80) -> str:
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", value or "").strip("_ .")
    return (value or "untitled")[:limit]


def _extract_canonical_id(info: dict[str, Any], url: CanonicalURL, platform: str) -> str:
    raw_id = str(info.get("id") or Path(url.path).name or "unknown")
    return f"{platform}:{raw_id}"


class YtDlpAdapter(Adapter):
    platforms = frozenset({
        "youtube", "x", "tiktok", "instagram", "facebook", "vimeo",
        "reddit", "pinterest", "twitch", "bilibili", "weibo",
        "douyin", "kuaishou", "xiaohongshu", "unknown",
    })
    capabilities = frozenset({"video", "image_set", "no_watermark"})

    def _base_opts(self) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "ignoreerrors": False,
        }
        if self.config.proxy:
            opts["proxy"] = self.config.proxy
        if self.config.cookies_file:
            opts["cookiefile"] = self.config.cookies_file
        elif self.config.cookies_browser:
            opts["cookiesfrombrowser"] = (self.config.cookies_browser,)
        return opts

    def _blocking_probe(self, url: str) -> dict[str, Any]:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError, ExtractorError

        try:
            with YoutubeDL(self._base_opts()) as ydl:
                return ydl.extract_info(url, download=False)
        except (DownloadError, ExtractorError) as exc:
            text = str(exc)
            stale_markers = (
                "Unsupported URL", "Unable to extract",
                "sign in", "login required", "cookies",
            )
            if any(marker.lower() in text.lower() for marker in stale_markers):
                raise AdapterStale(text) from exc
            raise AdapterError(text) from exc

    async def probe(self, url: CanonicalURL) -> Resource:
        info = await asyncio.to_thread(self._blocking_probe, url.raw_url)
        if not info:
            raise AdapterError("yt-dlp returned no metadata")

        extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
        no_wm = (
            "native"
            if any(name in extractor for name in _NATIVE_CLEAN_EXTRACTORS)
            else "unknown"
        )

        items: list[MediaItem] = []
        entries = info.get("entries")
        if entries:
            # playlists/carousels: flatten, but noplaylist above means this is rare
            for idx, entry in enumerate(entries or []):
                if not entry:
                    continue
                items.append(self._item_from_info(entry, idx, no_wm))
        else:
            items.append(self._item_from_info(info, 0, no_wm))

        platform = url.platform if url.platform != "unknown" else (extractor or "unknown")
        return Resource(
            canonical_id=_extract_canonical_id(info, url, platform),
            platform=platform,
            content_type=(
                "image_set"
                if info.get("_type") == "multi_video"
                and all(i.kind == "image" for i in items)
                else "video"
            ),
            page_url=info.get("webpage_url") or url.raw_url,
            title=info.get("title"),
            author=Author(
                id=str(info.get("channel_id")) if info.get("channel_id") else None,
                name=info.get("uploader") or info.get("channel"),
                url=info.get("uploader_url") or info.get("channel_url"),
            ),
            caption=info.get("description"),
            items=items,
            adapter_used=self.name,
        )

    @staticmethod
    def _item_from_info(info: dict[str, Any], index: int, no_wm: str) -> MediaItem:
        url_value = info.get("url")
        if info.get("vcodec") == "none" and info.get("acodec") != "none":
            kind = "audio"
        elif info.get("ext") in {"jpg", "jpeg", "png", "webp", "heic"}:
            kind = "image"
        else:
            kind = "video"
        duration = info.get("duration")
        return MediaItem(
            kind=kind,
            url=url_value,
            index=index,
            no_watermark=no_wm if kind == "video" else "native",
            width=info.get("width"),
            height=info.get("height"),
            duration_ms=int(duration * 1000) if duration else None,
            ext=info.get("ext"),
            codec=info.get("vcodec") if kind == "video" else info.get("acodec"),
            size_hint=info.get("filesize") or info.get("filesize_approx"),
        )

    def _blocking_download(self, url: str, out_dir: Path, quality: str) -> list[str]:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError, ExtractorError

        opts = self._base_opts()
        opts.pop("skip_download", None)
        opts.update({
            "format": _QUALITY_FORMAT.get(quality, _QUALITY_FORMAT["compat"]),
            "outtmpl": str(out_dir / "%(title).80B [%(id)s].%(ext)s"),
            "writethumbnail": False,
            "writedescription": False,
            "writeinfojson": True,
            "restrictfilenames": False,
            "merged_output_format": "mp4" if quality == "compat" else None,
        })
        before = {p.resolve() for p in out_dir.rglob("*") if p.is_file()}
        try:
            with YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
        except (DownloadError, ExtractorError) as exc:
            raise AdapterError(str(exc)) from exc
        after = {p.resolve() for p in out_dir.rglob("*") if p.is_file()}
        return sorted(str(p) for p in (after - before))

    async def download(self, resource: Resource, quality: str = "compat") -> list[str]:
        content_id = _slug(resource.canonical_id.split(":", 1)[-1])
        out_dir = self.config.ensure_home() / resource.platform / content_id
        out_dir.mkdir(parents=True, exist_ok=True)
        files = await asyncio.to_thread(
            functools.partial(self._blocking_download, resource.page_url, out_dir, quality)
        )
        # Persist the normalized metadata next to the bytes for cache/summary reuse.
        (out_dir / "resource.json").write_text(
            json.dumps(resource.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        media_files = [f for f in files if not f.endswith(".info.json")]
        # Counts may legitimately differ (merged streams, carousels), so no strict zip.
        for item, path in zip(resource.items, media_files, strict=False):
            item.local_path = path
        return [f for f in files if not f.endswith(".info.json")]
