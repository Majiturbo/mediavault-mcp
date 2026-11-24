"""f2 backed adapter for first-class CN-platform extraction.

f2 (https://github.com/Johnserf-Seed/f2) is an optional external CLI that
tracks Douyin / Xiaohongshu / Kuaishou / TikTok / Weibo / Bilibili page
shapes more aggressively than the generic yt-dlp extractor. This adapter is
deliberately thin and isolates every external assumption in two seams:

  * _which / _require_available - locate the binary; when it is absent we
    raise AdapterStale so the registry transparently falls back to yt-dlp.
  * _run + _probe_args / _download_args - the exact subprocess contract.
    The argv below targets the f2 CLI (app subcommand + -u url + -p path,
    -M for metadata-only). It is pinned in one place so a live smoke test
    against an installed f2 only has to adjust these builders.

All parsing is defensive: an unrecognised or empty metadata document is an
AdapterStale ("platform shape changed, try the next adapter"), never a
silent empty Resource. No-watermark stays "unknown" for video because we
cannot prove per-URL that the source stream lacks a burned-in overlay.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any

from mediavault.adapters.base import Adapter, AdapterError, AdapterStale
from mediavault.models import Author, MediaItem, Resource
from mediavault.routing import CanonicalURL

# platform id -> f2 app subcommand
_APP_COMMANDS = {
    "douyin": "dy",
    "xiaohongshu": "xhs",
    "kuaishou": "ks",
    "tiktok": "tk",
    "weibo": "wb",
    "bilibili": "bili",
}

# stderr substrings that mean the platform/CLI shape moved, not a hard block.
_STALE_MARKERS = ("login", "cookie", "sign", "version", "unsupported", "expired")
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic")
_VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".flv", ".mkv")


def _slug(value: str, limit: int = 80) -> str:
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", value or "").strip("_ .")
    return (value or "untitled")[:limit]


class F2Adapter(Adapter):
    platforms = frozenset(_APP_COMMANDS)
    capabilities = frozenset({"video", "image_set", "no_watermark"})

    # Explicitly outrank the generic yt-dlp adapter (base score 100) when the
    # f2 binary is installed; an unavailable adapter still degrades via Stale.
    def match_score(self, url: CanonicalURL) -> int:
        return 200 if url.platform in self.platforms else 10

    # ------------------------------------------------------------------ #
    # External seams (the only pieces a live f2 install may need to touch)
    # ------------------------------------------------------------------ #
    def _which(self) -> str | None:
        return shutil.which(self.config.f2_bin)

    def _require_available(self) -> str:
        binary = self._which()
        if binary is None:
            raise AdapterStale(
                f"f2 binary {self.config.f2_bin!r} not found on PATH"
            )
        return binary

    def _probe_args(self, binary: str, app: str, url: str, out_dir: Path) -> list[str]:
        # -M: write metadata without downloading the media bytes.
        return [binary, app, "-u", url, "-p", str(out_dir), "-M"]

    def _download_args(self, binary: str, app: str, url: str, out_dir: Path) -> list[str]:
        # f2 selects the platform's source stream; the yt-dlp quality presets
        # do not map onto f2 filters, so quality is best-effort here.
        return [binary, app, "-u", url, "-p", str(out_dir)]

    async def _run(
        self, args: list[str], out_dir: Path
    ) -> tuple[int, str, str]:
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
            return AdapterStale(f"f2 stale: {detail[:200]}")
        return AdapterError(f"f2 failed: {detail[:200]}")

    def _work_dir(self, url: CanonicalURL, kind: str) -> Path:
        return (
            self.config.ensure_home()
            / ".f2"
            / kind
            / url.platform
            / _slug(Path(url.path).name or url.raw_url)
        )

    # ------------------------------------------------------------------ #
    # Probe
    # ------------------------------------------------------------------ #
    async def probe(self, url: CanonicalURL) -> Resource:
        binary = self._require_available()
        app = _APP_COMMANDS.get(url.platform)
        if app is None:
            raise AdapterError(f"f2 has no app for platform {url.platform!r}")

        out_dir = self._work_dir(url, "probe")
        code, stdout, stderr = await self._run(
            self._probe_args(binary, app, url.raw_url, out_dir), out_dir
        )
        if code != 0:
            raise self._failure(stderr or stdout)

        meta_files = sorted(
            out_dir.rglob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not meta_files:
            raise AdapterStale("f2 wrote no metadata JSON document")
        try:
            payload = json.loads(meta_files[0].read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AdapterStale(f"f2 metadata is not valid JSON: {exc}") from exc

        return self._parse(payload, url)

    # ------------------------------------------------------------------ #
    # Download
    # ------------------------------------------------------------------ #
    async def download(self, resource: Resource, quality: str = "compat") -> list[str]:
        binary = self._require_available()
        app = _APP_COMMANDS.get(resource.platform)
        if app is None:
            raise AdapterError(f"f2 has no app for platform {resource.platform!r}")

        content_id = _slug(resource.canonical_id.split(":", 1)[-1])
        out_dir = self.config.ensure_home() / resource.platform / content_id
        out_dir.mkdir(parents=True, exist_ok=True)
        before = {p.resolve() for p in out_dir.rglob("*") if p.is_file()}

        code, stdout, stderr = await self._run(
            self._download_args(binary, app, resource.page_url, out_dir), out_dir
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

    # ------------------------------------------------------------------ #
    # Defensive metadata normalization (fixture-pinned contract)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _deep_find(obj: Any, keys: tuple[str, ...]) -> Any:
        if isinstance(obj, dict):
            for key in keys:
                value = obj.get(key)
                if value not in (None, "", [], {}):
                    return value
            for value in obj.values():
                found = F2Adapter._deep_find(value, keys)
                if found not in (None, "", [], {}):
                    return found
        elif isinstance(obj, list):
            for value in obj:
                found = F2Adapter._deep_find(value, keys)
                if found not in (None, "", [], {}):
                    return found
        return None

    @staticmethod
    def _walk_urls(obj: Any, out: list[str]) -> None:
        if isinstance(obj, str):
            if obj.startswith("http"):
                out.append(obj)
        elif isinstance(obj, dict):
            for value in obj.values():
                F2Adapter._walk_urls(value, out)
        elif isinstance(obj, list):
            for value in obj:
                F2Adapter._walk_urls(value, out)

    @staticmethod
    def _strip_query(url: str) -> str:
        return url.lower().split("?", 1)[0]

    def _parse(self, payload: Any, url: CanonicalURL) -> Resource:
        # f2 sometimes emits a one-element batch; unwrap defensively.
        if isinstance(payload, list):
            payload = next((item for item in payload if isinstance(item, dict)), {})
        if not isinstance(payload, dict):
            raise AdapterStale("f2 metadata has an unexpected top-level shape")

        title = self._deep_find(payload, ("desc", "title", "note_title", "caption"))
        author_name = self._deep_find(payload, ("nickname", "author_name", "name"))
        raw_id = self._deep_find(payload, ("aweme_id", "note_id", "id", "photo_id"))
        canonical_id = f"{url.platform}:{raw_id or Path(url.path).name or 'unknown'}"

        # f2 nests media several levels deep (e.g. video.play_addr.url_list,
        # image_list[].url). Collect every http URL recursively, then partition
        # by file extension so per-app schema drift only needs new scalar keys.
        all_urls: list[str] = []
        self._walk_urls(payload, all_urls)
        image_urls = [u for u in all_urls if self._strip_query(u).endswith(_IMAGE_EXT)]
        video_urls = [u for u in all_urls if self._strip_query(u).endswith(_VIDEO_EXT)]
        video_url = video_urls[0] if video_urls else None

        items: list[MediaItem] = []
        if image_urls:
            items.extend(
                MediaItem(kind="image", url=u, index=i, no_watermark="native")
                for i, u in enumerate(image_urls)
            )
        if video_url:
            items.append(
                MediaItem(kind="video", url=video_url, index=len(items), no_watermark="unknown")
            )
        if not items and not title:
            raise AdapterStale("f2 metadata contained no recognizable media or title")

        return Resource(
            canonical_id=canonical_id,
            platform=url.platform,
            content_type="image_set" if image_urls and not video_url else "video",
            page_url=url.raw_url,
            title=title if isinstance(title, str) else None,
            author=Author(name=author_name if isinstance(author_name, str) else None),
            caption=title if isinstance(title, str) else None,
            items=items,
            adapter_used=self.name,
        )
