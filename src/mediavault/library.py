"""On-disk media library: persistent evidence/summary cache.

Each probed resource owns one directory under MEDIAVAULT_HOME/<platform>/
<content_id>/. Adapters already write the media and resource.json there; this
module adds two JSON artifacts so repeated analyze/summarize calls can reuse
prior work instead of re-downloading and re-transcribing:

  evidence.json - the key-free analyze payload (caption, transcript, frames)
  summary.json  - the optional server-side fused summary

Writes are atomic (temp file + os.replace) so a crashed run never leaves a
half-written JSON document. Reads degrade to None instead of raising: a
corrupt or truncated cache is treated as a miss, never as fatal.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from mediavault.config import Config

EVIDENCE_FILE = "evidence.json"
SUMMARY_FILE = "summary.json"


def _slug(value: str, limit: int = 120) -> str:
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", value or "").strip("_ .")
    return (value or "unknown")[:limit]


def split_canonical(canonical_id: str) -> tuple[str, str]:
    """Split "platform:id" into (platform, content_id) with safe fallbacks."""
    if ":" in canonical_id:
        platform, content_id = canonical_id.split(":", 1)
        return platform or "unknown", content_id or "unknown"
    return "unknown", canonical_id or "unknown"


class MediaLibrary:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._home = config.home

    def resource_dir(self, canonical_id: str) -> Path:
        platform, content_id = split_canonical(canonical_id)
        return self._home / _slug(platform) / _slug(content_id)

    # ------------------------------------------------------------------ #
    # Atomic JSON primitives
    # ------------------------------------------------------------------ #
    @staticmethod
    def _write_json(path: Path, payload: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)
        return path

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------------ #
    # Evidence (analyze) cache
    # ------------------------------------------------------------------ #
    def evidence_path(self, canonical_id: str) -> Path:
        return self.resource_dir(canonical_id) / EVIDENCE_FILE

    def save_evidence(self, canonical_id: str, payload: dict) -> Path:
        return self._write_json(self.evidence_path(canonical_id), payload)

    def load_evidence(self, canonical_id: str) -> dict | None:
        return self._read_json(self.evidence_path(canonical_id))

    def frames_present(self, payload: dict) -> bool:
        """True when every keyframe referenced by cached evidence still exists."""
        frames = payload.get("frame_paths") or []
        return bool(frames) and all(Path(p).is_file() for p in frames)

    def reusable_evidence(self, canonical_id: str, *, with_media: bool) -> dict | None:
        """Return cached evidence only when it still satisfies the request.

        A metadata-only request (with_media=False) accepts any cache. A media
        request additionally requires the extracted frame files to still be on
        disk; otherwise the evidence cannot back the image content blocks and
        the caller must recompute.
        """
        cached = self.load_evidence(canonical_id)
        if cached is None:
            return None
        if with_media and not self.frames_present(cached):
            return None
        cached["cached"] = True
        return cached

    # ------------------------------------------------------------------ #
    # Fused summary cache
    # ------------------------------------------------------------------ #
    def summary_path(self, canonical_id: str) -> Path:
        return self.resource_dir(canonical_id) / SUMMARY_FILE

    def save_summary(self, canonical_id: str, summary: dict) -> Path:
        return self._write_json(self.summary_path(canonical_id), summary)

    def load_summary(self, canonical_id: str) -> dict | None:
        return self._read_json(self.summary_path(canonical_id))

    # ------------------------------------------------------------------ #
    # Library index
    # ------------------------------------------------------------------ #
    def index(self) -> list[dict]:
        """List every cached resource with the artifacts available on disk."""
        if not self._home.is_dir():
            return []
        entries: list[dict] = []
        for evidence_file in sorted(self._home.rglob(EVIDENCE_FILE)):
            payload = self._read_json(evidence_file)
            if payload is None:
                continue
            resource_dir = evidence_file.parent
            entries.append({
                "canonical_id": payload.get("canonical_id"),
                "platform": payload.get("platform"),
                "title": payload.get("title"),
                "has_summary": (resource_dir / SUMMARY_FILE).is_file(),
                "frames_extracted": payload.get("frames_extracted", 0),
                "cached_at": payload.get("analyzed_at"),
                "path": str(resource_dir),
            })
        return entries
