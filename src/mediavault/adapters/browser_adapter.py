"""Browser-backed adapter (M4): local browser clears JS-VM challenges.

Douyin authorizes its detail API only for requests carrying the full browser
environment (TLS/JS fingerprint plus ambient signing headers and cookies); a
command-line client such as yt-dlp is rejected even when handed the complete
cookie jar. This adapter runs an in-page fetch inside a local real browser
(which executes the JS-VM challenge anonymously, with no login) and parses the
returned aweme_detail. The signed CDN URL is then downloaded directly over
httpx with browser-like headers.

Playwright missing, the opt-in switch off, or an uncleared challenge all raise
AdapterStale so the registry falls through to f2/gallery-dl/yt-dlp unchanged.
"""

from __future__ import annotations

import re

import httpx

from mediavault.adapters.base import Adapter, AdapterError, AdapterStale
from mediavault.browser_session import (
    BrowserChallengeError,
    BrowserUnavailable,
    build_bootstrapper,
)
from mediavault.models import MediaItem, Resource
from mediavault.network import normalize_proxy
from mediavault.platform_aweme import AWEME_DETAIL_JS, parse_aweme_detail
from mediavault.routing import CanonicalURL

# Direct in-browser extraction is implemented for Douyin; other JS-challenge
# platforms keep using the f2 -> gallery-dl -> yt-dlp chain until mapped.
_BROWSER_PLATFORMS = frozenset({"douyin"})
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _slug(value: str, limit: int = 80) -> str:
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", value or "").strip("_ .")
    return (value or "untitled")[:limit]


class BrowserYtDlpAdapter(Adapter):
    platforms = _BROWSER_PLATFORMS
    capabilities = frozenset({"video", "image_set", "no_watermark"})

    def match_score(self, url: CanonicalURL) -> int:
        # Outrank f2 (200) while enabled; only registered when opted in.
        return 220 if url.platform in self.platforms else 10

    async def _fetch_detail(self, url: CanonicalURL):
        bootstrapper = build_bootstrapper(self.config)
        if bootstrapper is None:
            raise AdapterStale("browser bootstrap is disabled")
        try:
            return await bootstrapper.evaluate_after_ready(
                url.platform, url.raw_url, AWEME_DETAIL_JS
            )
        except BrowserUnavailable as exc:
            raise AdapterStale(f"browser unavailable: {exc}") from exc
        except BrowserChallengeError as exc:
            raise AdapterStale(f"browser challenge not cleared: {exc}") from exc

    async def probe(self, url: CanonicalURL) -> Resource:
        detail = await self._fetch_detail(url)
        resource = parse_aweme_detail(detail, url.raw_url, self.name)
        resource.notes.append(
            "browser:anonymous in-page detail fetch (no account login)"
        )
        return resource

    async def download(self, resource: Resource, quality: str = "compat") -> list[str]:
        content_id = _slug(resource.canonical_id.split(":", 1)[-1])
        out_dir = self.config.ensure_home() / resource.platform / content_id
        out_dir.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": _BROWSER_UA, "Referer": resource.page_url}
        proxy = normalize_proxy(self.config.proxy)
        files: list[str] = []
        async with httpx.AsyncClient(
            headers=headers, proxy=proxy, follow_redirects=True,
            timeout=self.config.browser_timeout_ms / 1000,
        ) as client:
            for index, item in enumerate(resource.items):
                if not item.url:
                    continue
                suffix = ".jpg" if item.kind == "image" else f".{item.ext or 'mp4'}"
                target = out_dir / f"{content_id}_{index}{suffix}"
                try:
                    await self._stream(client, item, target)
                except httpx.HTTPError as exc:
                    raise AdapterError(f"browser CDN download failed: {exc}") from exc
                item.local_path = str(target)
                files.append(str(target))
        if not files:
            raise AdapterError("browser adapter found no downloadable media URLs")
        return files

    @staticmethod
    async def _stream(client: httpx.AsyncClient, item: MediaItem, target) -> None:
        async with client.stream("GET", item.url) as response:
            response.raise_for_status()
            with open(target, "wb") as handle:
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)