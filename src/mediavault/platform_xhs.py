"""Pure parsing of a Xiaohongshu web note into a normalized Resource.

The share page renders window.__INITIAL_STATE__ with note.noteDetailMap.
A thin in-page script returns the raw note object; this module turns that
shape into a Resource so the platform contract is testable offline without
Playwright. On a risk-controlled/anonymous-unsupported network the page has
no __INITIAL_STATE__, the script returns null and the caller degrades via
AdapterStale rather than guessing.
"""

from __future__ import annotations

from typing import Any

from mediavault.adapters.base import AdapterStale
from mediavault.models import Author, MediaItem, Resource

# Runs inside the page after navigation. Anonymous share pages carry an
# xsec_token in the URL; when the network is trusted the note is present in
# __INITIAL_STATE__ without login. Returns null on login/error pages.
XHS_NOTE_JS = """
() => {
  try {
    const map = window.__INITIAL_STATE__
      && window.__INITIAL_STATE__.note
      && window.__INITIAL_STATE__.note.noteDetailMap;
    if (!map) return null;
    const key = Object.keys(map)[0];
    return key && map[key] && map[key].note ? map[key].note : null;
  } catch (e) {
    return null;
  }
}
"""


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _first_h264(video: dict) -> dict | None:
    stream = (video.get("media") or {}).get("stream") or {}
    h264 = stream.get("h264")
    if isinstance(h264, list) and h264 and isinstance(h264[0], dict):
        return h264[0]
    return None


def parse_xhs_note(note: Any, page_url: str, adapter_name: str) -> Resource:
    if not isinstance(note, dict) or not note.get("noteId"):
        raise AdapterStale("xhs note missing (risk control, login wall or shape changed)")

    note_id = str(note["noteId"])
    images = note.get("imageList") if isinstance(note.get("imageList"), list) else []
    video = note.get("video") if isinstance(note.get("video"), dict) else {}
    user = note.get("user") if isinstance(note.get("user"), dict) else {}

    items: list[MediaItem] = []
    for index, image in enumerate(images):
        if not isinstance(image, dict):
            continue
        url = image.get("urlDefault") or image.get("url")
        if isinstance(url, str) and url:
            items.append(MediaItem(
                kind="image", url=url, index=index, no_watermark="unknown",
                width=_as_int(image.get("width")), height=_as_int(image.get("height")),
                ext="jpg",
            ))

    h264 = _first_h264(video)
    if h264 and isinstance(h264.get("masterUrl"), str):
        items.append(MediaItem(
            kind="video", url=h264["masterUrl"], index=len(items),
            # XHS web h264 stream is not the app's logo-saved export, but we
            # cannot prove per-URL cleanliness, so never claim removal.
            no_watermark="unknown",
            width=_as_int(h264.get("width")), height=_as_int(h264.get("height")),
            ext="mp4",
        ))

    title = note.get("title") if isinstance(note.get("title"), str) and note.get("title") else None
    desc = note.get("desc") if isinstance(note.get("desc"), str) else None
    if not items:
        raise AdapterStale("xhs note contained no recognizable images or video")

    content_type = "video" if any(i.kind == "video" for i in items) else "image_set"
    return Resource(
        canonical_id=f"xhs:{note_id}",
        platform="xiaohongshu",
        content_type=content_type,
        page_url=page_url,
        title=title or (desc.splitlines()[0] if desc else None),
        caption=desc,
        author=Author(
            id=str(user["userId"]) if user.get("userId") else None,
            name=user.get("nickname") if isinstance(user.get("nickname"), str) else None,
        ),
        items=items,
        adapter_used=adapter_name,
    )