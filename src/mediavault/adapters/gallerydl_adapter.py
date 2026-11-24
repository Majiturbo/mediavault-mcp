"""gallery-dl backed adapter for image-set posts.

gallery-dl (https://github.com/mikf/gallery-dl) tracks image extractors for
Xiaohongshu, Instagram, Pinterest, Weibo and hundreds of galleries. Like the
f2 adapter it is an optional external CLI: the binary lookup and subprocess
contract are isolated in seams, and a missing binary or drifted extractor
raises AdapterStale so the registry falls through to yt-dlp.

Contract pinned here (adjust only these builders against a live install):
  probe    : gallery-dl -g <url>            prints direct media URLs, no bytes
  download : gallery-dl -D <dir> <url>      writes media under <dir>

Output lines are partitioned by file extension into images and videos. An
empty or unrecognizable probe result is AdapterStale, never an empty
Resource. Image items report no_watermark "native" (source images), matching
the yt-dlp adapter convention; anything unverifiable stays "unknown".
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path

from mediavault.adapters.base import Adapter, AdapterError, AdapterStale
from mediavault.models import Author, MediaItem, Resource
from mediavault.network import NetworkConfigError, gallerydl_network_opts
from mediavault.routing import CanonicalURL

_PLATFORMS = frozenset({
    "xiaohongshu", "instagram", "pinterest", "weibo",
})

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif", ".avif")
_VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
_STALE_MARKERS = ("login", "cookie", "unsupported", "not supported",
                  "no extractor", "404", "not found", "forbidden", "private")


def _slug(value: str, limit: int = 120) -> str:
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", value or "").strip("_ .")
    return (value or "untitled")[:limit]


class GalleryDLAdapter(Adapter):
    platforms = _PLATFORMS
    capabilities = frozenset({"image_set", "no_watermark"})

    # Below f2 (200) which owns first-class CN video extraction, above the
    # generic yt-dlp adapter (100).
    def match_score(self, url: CanonicalURL) -> int:
        return 150 if url.platform in self.platforms else 10

    # ------------------------------------------------------------------ #
    # External seams
    # ------------------------------------------------------------------ #
    def _which(self) -> str | None:
        return shutil.which(self.config.gallerydl_bin)

    def _require_available(self) -> str:
        binary = self._which()
        if binary is None:
            raise AdapterStale(
                f"gallery-dl binary {self.config.gallerydl_bin!r} not found on PATH"
            )
        return binary

    def _network_opts(self) -> tuple[list[str], list[str]]:
        try:
            return gallerydl_network_opts(self.config)
        except NetworkConfigError as exc:
            raise AdapterError(str(exc)) from exc

    def _probe_args(self, binary: str, url: str, net_opts: list[str]) -> list[str]:
        # -g / --get-urls: print direct URLs without downloading.
        return [binary, *net_opts, "-g", url]

    def _download_args(
        self, binary: str, url: str, out_dir: Path, net_opts: list[str]
    ) -> list[str]:
        return [binary, *net_opts, "-D", str(out_dir), url]

    async def _run(self, args: list[str], out_dir: Path) -> tuple[int, str, str]:
        out_dir.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=out_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return (
            proc.returncode or 0,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )

    def _failure(self, detail: str) -> AdapterError:
        text = (detail or "").lower()
        if any(marker in text for marker in _STALE_MARKERS):
            return AdapterStale(f"gallery-dl stale: {detail[:200]}")
        return AdapterError(f"gallery-dl failed: {detail[:200]}")

    @staticmethod
    def _partition_urls(stdout: str) -> tuple[list[str], list[str]]:
        images: list[str] = []
        videos: list[str] = []
        for line in stdout.splitlines():
            url = line.strip()
            if not url.startswith("http"):
                continue
            path = url.lower().split("?", 1)[0]
            if path.endswith(_IMAGE_EXT):
                images.append(url)
            elif path.endswith(_VIDEO_EXT):
                videos.append(url)
        # Preserve first-seen order while removing duplicates.
        return list(dict.fromkeys(images)), list(dict.fromkeys(videos))

    # ------------------------------------------------------------------ #
    # Adapter protocol
    # ------------------------------------------------------------------ #
    async def probe(self, url: CanonicalURL) -> Resource:
        binary = self._require_available()
        net_opts, warnings = self._network_opts()
        work_dir = self.config.ensure_home() / ".gallery-dl" / "probe" / url.platform
        code, stdout, stderr = await self._run(
            self._probe_args(binary, url.raw_url, net_opts), work_dir
        )
        if code != 0:
            raise self._failure(stderr or stdout)

        images, videos = self._partition_urls(stdout)
        if not images and not videos:
            raise AdapterStale("gallery-dl returned no recognizable media URLs")

        items: list[MediaItem] = []
        items.extend(
            MediaItem(kind="image", url=u, index=i, no_watermark="native")
            for i, u in enumerate(images)
        )
        items.extend(
            MediaItem(kind="video", url=u, index=len(items) + i, no_watermark="unknown")
            for i, u in enumerate(videos)
        )
        resource = Resource(
            canonical_id=self._canonical_id(url),
            platform=url.platform,
            content_type="image_set" if not videos else "mixed",
            page_url=url.raw_url,
            title=None,
            author=Author(),
            items=items,
            adapter_used=self.name,
        )
        resource.notes.extend(warnings)
        return resource

    async def download(self, resource: Resource, quality: str = "compat") -> list[str]:
        binary = self._require_available()
        net_opts, warnings = self._network_opts()
        resource.notes.extend(warnings)
        content_id = _slug(resource.canonical_id.split(":", 1)[-1])
        out_dir = self.config.ensure_home() / resource.platform / content_id
        out_dir.mkdir(parents=True, exist_ok=True)
        before = {p.resolve() for p in out_dir.rglob("*") if p.is_file()}

        code, stdout, stderr = await self._run(
            self._download_args(binary, resource.page_url, out_dir, net_opts), out_dir
        )
        if code != 0:
            raise self._failure(stderr or stdout)

        after = {p.resolve() for p in out_dir.rglob("*") if p.is_file()}
        (out_dir / "resource.json").write_text(
            json.dumps(resource.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        files = sorted(str(p) for p in (after - before))
        media_files = [f for f in files if not f.endswith(".json")]
        for item, path in zip(resource.items, media_files, strict=False):
            item.local_path = path
        return media_files

    @staticmethod
    def _canonical_id(url: CanonicalURL) -> str:
        # gallery-dl does not return a platform id from -g; use the last path
        # component, falling back to the host when the path is empty.
        tail = url.path.rstrip("/").rsplit("/", 1)[-1] or url.host or "unknown"
        return f"{url.platform}:{_slug(tail)}"
