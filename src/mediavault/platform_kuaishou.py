"""Pure parsing of a Kuaishou web short-video page into a normalized Resource.

The short-video page hydrates window.__APOLLO_STATE__ (an Apollo GraphQL
client cache); the detail object lives under defaultClient at a key prefixed
VisionVideoDetailPhoto:. A thin in-page script returns that raw object and
this module maps it to a Resource, fully testable offline. On a risk-controlled
network the page is a bare {"result": 2} body with no Apollo state, so the
script returns null and the caller degrades via AdapterStale instead of
guessing. The exact live shape could not be verified from a datacenter IP;
parsing is therefore defensive and Stale-guarded.
"""

from __future__ import annotations

from typing import Any

from mediavault.adapters.base import AdapterStale
from mediavault.models import Author, MediaItem, Resource

# In-page extraction: locate the VisionVideoDetailPhoto cache entry. Returns
# null on risk/login pages that never hydrate the Apollo cache.
KS_PHOTO_JS = """
() => {
  try {
    const cache = window.__APOLLO_STATE__ && window.__APOLLO_STATE__.defaultClient;
    if (!cache) return null;
    const key = Object.keys(cache).find((k) => k.startsWith('VisionVideoDetailPhoto:'));
    return key ? cache[key] : null;
  } catch (e) {
    return null;
  }
}
"""

def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _bitrate(rep: dict) -> int:
    value = rep.get("bitrate")
    return value if isinstance(value, int) else -1


def _manifest_candidates(photo_url: Any) -> list[dict]:
    """Flatten an Apollo photoUrl manifest into representation dicts."""
    if not isinstance(photo_url, dict):
        return []
    reps: list[dict] = []
    for adaptation in photo_url.get("adaptationSet") or []:
        if not isinstance(adaptation, dict):
            continue
        for rep in adaptation.get("representation") or []:
            if isinstance(rep, dict) and isinstance(rep.get("url"), str):
                reps.append(rep)
    return reps


def parse_kuaishou_photo(photo: Any, page_url: str, adapter_name: str) -> Resource:
    if not isinstance(photo, dict) or not photo.get("photoId"):
        raise AdapterStale("kuaishou photo missing (risk control, login wall or shape changed)")

    photo_id = str(photo["photoId"])
    user = photo.get("author") if isinstance(photo.get("author"), dict) else {}

    items: list[MediaItem] = []
    photo_url = photo.get("photoUrl")
    if isinstance(photo_url, str) and photo_url:
        items.append(MediaItem(
            kind="video", url=photo_url, index=0, no_watermark="unknown",
            width=_as_int(photo.get("width")), height=_as_int(photo.get("height")),
            duration_ms=_as_int(photo.get("duration")), ext="mp4",
        ))
    else:
        reps = _manifest_candidates(photo_url)
        if reps:
            best = max(reps, key=_bitrate)
            items.append(MediaItem(
                kind="video", url=best["url"], index=0, no_watermark="unknown",
                width=_as_int(best.get("width")), height=_as_int(best.get("height")),
                duration_ms=_as_int(photo.get("duration")), ext="mp4",
            ))

    caption = photo.get("caption") if isinstance(photo.get("caption"), str) else None
    if not items:
        raise AdapterStale("kuaishou photo contained no playable video URL")

    return Resource(
        canonical_id=f"kuaishou:{photo_id}",
        platform="kuaishou",
        content_type="video",
        page_url=page_url,
        title=caption,
        caption=caption,
        author=Author(
            id=str(user["id"]) if user.get("id") else None,
            name=user.get("name") if isinstance(user.get("name"), str) else None,
        ),
        items=items,
        adapter_used=adapter_name,
    )