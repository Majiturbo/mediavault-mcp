"""Binary-free SSR adapter: parse server-rendered state over plain HTTP.

Unlike the M4 browser adapter this never launches Chromium. It requests the
share document with a browser-like User-Agent and reads the state already
embedded in the SSR HTML (Xiaohongshu __INITIAL_STATE__, Kuaishou
__APOLLO_STATE__). On a trusted residential network that state can contain the
detail object before any JavaScript runs; on risk-control/login pages it only
holds shell state, the pure extractor returns None and the adapter raises
AdapterStale so the registry falls through to f2/gallery-dl/yt-dlp.

It deliberately ranks 175, below the first-class f2 CLI (200) and above
gallery-dl (150): it needs no external binary, but is promoted above f2 only
after a live residential-network success path is verified. Media downloads
reuse the browser adapter's chunked httpx streamer.
"""

from __future__ import annotations

import httpx

from mediavault.adapters.base import Adapter, AdapterError, AdapterStale
from mediavault.adapters.browser_adapter import _BROWSER_UA, BrowserYtDlpAdapter, _slug
from mediavault.models import Resource
from mediavault.network import normalize_proxy
from mediavault.platform_kuaishou import parse_kuaishou_photo
from mediavault.platform_xhs import parse_xhs_note
from mediavault.routing import CanonicalURL
from mediavault.ssr_state import extract_kuaishou_photo, extract_xhs_note

# platform -> (SSR state extractor, pure Resource parser)
_SSR_PARSERS = {
    "xiaohongshu": (extract_xhs_note, parse_xhs_note),
    "kuaishou": (extract_kuaishou_photo, parse_kuaishou_photo),
}


class SsrAdapter(Adapter):
    platforms = frozenset(_SSR_PARSERS)
    capabilities = frozenset({"video", "image_set", "no_watermark"})

    def match_score(self, url: CanonicalURL) -> int:
        return 175 if url.platform in self.platforms else 10

    async def probe(self, url: CanonicalURL) -> Resource:
        entry = _SSR_PARSERS.get(url.platform)
        if entry is None:  # supports() normally prevents this
            raise AdapterStale(f"ssr extraction not mapped for {url.platform}")
        extractor, parser = entry
        headers = {
            "User-Agent": _BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            async with httpx.AsyncClient(
                headers=headers, proxy=normalize_proxy(self.config.proxy),
                follow_redirects=True, timeout=self.config.ssr_timeout_ms / 1000,
            ) as client:
                response = await client.get(url.raw_url)
                response.raise_for_status()
                html = response.text
        except httpx.HTTPError as exc:
            raise AdapterStale(f"ssr fetch failed: {exc}") from exc
        raw = extractor(html)  # None on shell/risk pages -> parser raises Stale
        resource = parser(raw, url.raw_url, self.name)
        resource.notes.append(
            "ssr:anonymous server-rendered state (no browser, no account login)"
        )
        return resource

    async def download(self, resource: Resource, quality: str = "compat") -> list[str]:
        content_id = _slug(resource.canonical_id.split(":", 1)[-1])
        out_dir = self.config.ensure_home() / resource.platform / content_id
        out_dir.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": _BROWSER_UA, "Referer": resource.page_url}
        files: list[str] = []
        async with httpx.AsyncClient(
            headers=headers, proxy=normalize_proxy(self.config.proxy),
            follow_redirects=True, timeout=self.config.ssr_timeout_ms / 1000,
        ) as client:
            for index, item in enumerate(resource.items):
                if not item.url:
                    continue
                suffix = ".jpg" if item.kind == "image" else f".{item.ext or 'mp4'}"
                target = out_dir / f"{content_id}_{index}{suffix}"
                try:
                    await BrowserYtDlpAdapter._stream(client, item, target)
                except httpx.HTTPError as exc:
                    raise AdapterError(f"ssr CDN download failed: {exc}") from exc
                item.local_path = str(target)
                files.append(str(target))
        if not files:
            raise AdapterError("ssr adapter found no downloadable media URLs")
        return files