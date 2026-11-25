"""Extract hydrated platform state from server-rendered HTML without a browser.

Xiaohongshu and Kuaishou inline their initial client state as a JSON-ish object
literal assigned to window.__INITIAL_STATE__ / window.__APOLLO_STATE__ inside the
SSR document. On a trusted network the detail object is present before any JS
runs, so a plain HTTP client can parse it; risk-control and login pages render
only shell state with no detail, in which case these helpers return None and the
adapter degrades via AdapterStale instead of guessing.

The literals are not always strict JSON (Xiaohongshu may emit bare
``undefined``), so extraction is a brace-matching scan followed by a tolerant
decode. No JavaScript is ever executed.
"""

from __future__ import annotations

import json
import re
from typing import Any

_UNDEFINED = re.compile(r"\bundefined\b")


def balanced_object(html: str, marker: str) -> str | None:
    """Return the balanced ``{...}`` literal following ``marker`` in html."""
    start = html.find(marker)
    if start < 0:
        return None
    open_idx = html.find("{", start)
    if open_idx < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(open_idx, len(html)):
        char = html[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return html[open_idx:index + 1]
    return None  # unbalanced/truncated document


def _decode(literal: str | None) -> dict[str, Any] | None:
    if not literal:
        return None
    try:
        obj = json.loads(_UNDEFINED.sub("null", literal))
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def extract_xhs_note(html: str) -> dict[str, Any] | None:
    """Return the first hydrated Xiaohongshu note, or None on shell pages."""
    state = _decode(balanced_object(html, "__INITIAL_STATE__"))
    if not state:
        return None
    note_map = (state.get("note") or {}).get("noteDetailMap")
    if not isinstance(note_map, dict) or not note_map:
        return None
    first = next(iter(note_map.values()))
    note = first.get("note") if isinstance(first, dict) else None
    return note if isinstance(note, dict) else None


def extract_kuaishou_photo(html: str) -> dict[str, Any] | None:
    """Return the hydrated VisionVideoDetailPhoto, or None when absent."""
    state = _decode(balanced_object(html, "__APOLLO_STATE__"))
    if not state:
        return None
    cache = state.get("defaultClient")
    if not isinstance(cache, dict):
        return None
    for key, value in cache.items():
        if key.startswith("VisionVideoDetailPhoto:") and isinstance(value, dict):
            return value
    return None