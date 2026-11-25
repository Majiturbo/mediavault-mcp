"""Pure parsing of a Douyin aweme_detail document into a normalized Resource.

Separated from the browser adapter so the platform JSON contract is testable
offline without Playwright. The browser obtains aweme_detail via an in-page
fetch (external clients are blocked); only the shape parsing lives here.
"""

from __future__ import annotations

from typing import Any

from mediavault.adapters.base import AdapterStale
from mediavault.models import Author, MediaItem, Resource

# Runs inside the page after the JS challenge clears: same-origin fetch inherits
# the ambient fingerprint, signing headers and cookies that authorize the call.
AWEME_DETAIL_JS = """
async () => {
  const m = location.pathname.match(/\\/video\\/(\\d+)/);
  if (!m) return null;
  const url = 'https://www.douyin.com/aweme/v1/web/aweme/detail/'
    + '?aweme_id=' + m[1] + '&device_platform=webapp&aid=6383&channel=channel_pc_web';
  const r = await fetch(url, {credentials: 'include', headers: {accept: 'application/json'}});
  if (!r.ok) return null;
  const data = await r.json();
  return data && data.aweme_detail ? data.aweme_detail : null;
}
"""


def _first_url(node: Any) -> str | None:
    urls = (node or {}).get("url_list") if isinstance(node, dict) else None
    if isinstance(urls, list) and urls and isinstance(urls[0], str):
        return urls[0]
    return None


def _as_int(node: Any, key: str) -> int | None:
    value = node.get(key) if isinstance(node, dict) else None
    return value if isinstance(value, int) else None


def parse_aweme_detail(detail: Any, page_url: str, adapter_name: str) -> Resource:
    if not isinstance(detail, dict) or not detail.get("aweme_id"):
        raise AdapterStale("browser fetch returned no aweme_detail (blocked or shape changed)")

    aweme_id = str(detail["aweme_id"])
    video = detail.get("video") if isinstance(detail.get("video"), dict) else {}
    images = detail.get("images") if isinstance(detail.get("images"), list) else []
    author_raw = detail.get("author") if isinstance(detail.get("author"), dict) else {}

    items: list[MediaItem] = []
    for index, image in enumerate(images):
        url = _first_url(image)
        if url:
            items.append(MediaItem(kind="image", url=url, index=index, no_watermark="unknown"))

    play = _first_url(video.get("play_addr"))
    if play:
        play_addr = video.get("play_addr") or {}
        items.append(MediaItem(
            kind="video",
            url=play,
            index=len(items),
            # Never claim watermark removal we cannot prove per URL.
            no_watermark="unknown",
            width=_as_int(play_addr, "width"),
            height=_as_int(play_addr, "height"),
            duration_ms=_as_int(video, "duration"),
            size_hint=_as_int(play_addr, "data_size"),
            ext="mp4",
        ))

    title = detail.get("desc") if isinstance(detail.get("desc"), str) else None
    if not items and not title:
        raise AdapterStale("aweme_detail contained no recognizable media or title")

    return Resource(
        canonical_id=f"douyin:{aweme_id}",
        platform="douyin",
        content_type="image_set" if images and not play else "video",
        page_url=page_url,
        title=title,
        caption=title,
        author=Author(
            id=str(author_raw["sec_uid"]) if author_raw.get("sec_uid") else None,
            name=author_raw.get("nickname")
            if isinstance(author_raw.get("nickname"), str) else None,
        ),
        items=items,
        adapter_used=adapter_name,
    )