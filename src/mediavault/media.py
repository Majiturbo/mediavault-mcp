"""ffmpeg/ffprobe wrappers: audio extraction and scene-spread keyframes.

Everything runs as an async subprocess (no new heavy Python dependency) and
produces ASR/VLM friendly artifacts: 16 kHz mono WAV for transcription and
downscaled JPEG frames spread evenly across the video for vision analysis.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SCENE_THRESHOLD = 0.4
# Minimum spacing between two scene cuts / planned keyframes, in seconds.
_MIN_SCENE_GAP_S = 0.5
_SCENE_PTS_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


class MediaToolError(Exception):
    """ffprobe/ffmpeg is missing or returned a non-zero exit code."""


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise MediaToolError(
            f"{binary} not found on PATH; install ffmpeg (e.g. 'brew install ffmpeg')"
        )
    return path


@dataclass(frozen=True)
class MediaProbe:
    duration_ms: int | None
    has_audio: bool
    has_video: bool


async def _run(cmd: list[str]) -> str:
    stdout, _stderr = await _run_capture(cmd)
    return stdout


async def _run_capture(cmd: list[str]) -> tuple[str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace")[-800:]
        raise MediaToolError(f"{cmd[0]} exited {proc.returncode}: {detail}")
    return (
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def probe_media(path: str | Path) -> MediaProbe:
    ffprobe = _require("ffprobe")
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    data = json.loads(await _run(cmd))
    streams = data.get("streams", [])
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    has_video = any(s.get("codec_type") == "video" for s in streams)
    duration_ms: int | None = None
    raw_duration = data.get("format", {}).get("duration")
    if raw_duration:
        try:
            duration_ms = int(float(raw_duration) * 1000)
        except (TypeError, ValueError):
            duration_ms = None
    return MediaProbe(duration_ms=duration_ms, has_audio=has_audio, has_video=has_video)


async def extract_audio(video_path: str | Path, out_path: str | Path) -> Path:
    """Extract a 16 kHz mono PCM WAV, the universal ASR input."""
    ffmpeg = _require("ffmpeg")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(out),
    ]
    await _run(cmd)
    if not out.is_file() or out.stat().st_size == 0:
        raise MediaToolError(f"audio extraction produced no output: {out}")
    return out


@dataclass(frozen=True)
class KeyframeResult:
    frames: list[Path]
    scene_cuts: int


async def detect_scene_cuts(
    video_path: str | Path, *, threshold: float = DEFAULT_SCENE_THRESHOLD
) -> list[float]:
    """Return scene-cut timestamps (seconds) via ffmpeg showinfo metadata.

    showinfo logs pts_time on stderr while the null muxer discards decoded
    frames. Cuts closer than _MIN_SCENE_GAP_S are collapsed to avoid dupes.
    """
    ffmpeg = _require("ffmpeg")
    cmd = [
        ffmpeg, "-hide_banner", "-i", str(video_path),
        "-vf", f"select='gt(scene,{float(threshold)})',showinfo",
        "-f", "null", "-",
    ]
    _stdout, stderr = await _run_capture(cmd)
    cuts: list[float] = []
    for match in _SCENE_PTS_RE.finditer(stderr):
        ts = round(float(match.group(1)), 3)
        if not cuts or ts - cuts[-1] >= _MIN_SCENE_GAP_S:
            cuts.append(ts)
    return cuts


def plan_keyframe_timestamps(
    duration_s: float,
    max_frames: int,
    scene_cuts: list[float],
    *,
    include_anchors: bool = True,
    min_gap_s: float = _MIN_SCENE_GAP_S,
) -> list[float]:
    """Merge uniform anchors with scene cuts inside the frame budget.

    Pure function (no ffmpeg) so the denser, budget-capped selection is fully
    unit-testable; the first and last planned timestamps are always kept.
    """
    budget = max(1, min(int(max_frames), 32))
    if not duration_s or duration_s <= 0:
        return []
    anchors = (
        [round(duration_s * i / budget, 3) for i in range(budget)]
        if include_anchors
        else []
    )
    in_range = [round(t, 3) for t in scene_cuts if 0 <= t <= duration_s]
    merged = sorted(set(anchors + in_range))

    kept: list[float] = []
    for ts in merged:
        if not kept or ts - kept[-1] >= min_gap_s:
            kept.append(ts)
    if len(kept) <= budget:
        return kept

    if budget == 1:
        return [kept[0]]
    first, last = kept[0], kept[-1]
    interior = kept[1:-1]
    # len(kept) > budget guarantees want < len(interior) here, so downsample
    # evenly across the interior while always preserving both ends.
    want = budget - 2
    step = len(interior) / want
    chosen = [interior[int(i * step)] for i in range(want)]
    return sorted(set([first, *chosen, last]))


async def _extract_at_timestamps(
    video_path: str | Path,
    out_dir: Path,
    timestamps: list[float],
    *,
    width: int,
) -> list[Path]:
    """Grab one frame near each planned timestamp in a single ffmpeg pass."""
    ffmpeg = _require("ffmpeg")
    epsilon = 0.12
    terms = [
        f"between(t,{max(0.0, ts - epsilon):.2f},{ts + epsilon:.2f})"
        for ts in timestamps
    ]
    vf = f"select='{'+'.join(terms)}',scale={width}:-2"
    cmd = [
        ffmpeg, "-y", "-i", str(video_path),
        "-vf", vf, "-vsync", "vfr", "-q:v", "3",
        str(out_dir / "frame_%03d.jpg"),
    ]
    await _run(cmd)
    return [p for p in sorted(out_dir.glob("frame_*.jpg")) if p.stat().st_size > 0]


async def _interval_keyframes(
    video_path: str | Path,
    out: Path,
    *,
    max_frames: int,
    width: int,
    probe: MediaProbe,
) -> list[Path]:
    """The original evenly-spread fps-filter extraction (safe default)."""
    ffmpeg = _require("ffmpeg")
    frames = max(1, min(max_frames, 32))
    if probe.duration_ms and probe.duration_ms > 0:
        duration_s = probe.duration_ms / 1000
        fps = max(frames / duration_s, 0.05)
    else:
        fps = 1.0  # duration unknown: one frame per second, capped by frames count

    pattern = out / "frame_%03d.jpg"
    vf = f"fps={fps:.4f},scale={width}:-2"
    cmd = [
        ffmpeg, "-y", "-i", str(video_path),
        "-vf", vf, "-frames:v", str(frames), "-q:v", "3", str(pattern),
    ]
    await _run(cmd)
    return [p for p in sorted(out.glob("frame_*.jpg")) if p.stat().st_size > 0]


async def extract_keyframes_detailed(
    video_path: str | Path,
    out_dir: str | Path,
    *,
    max_frames: int,
    width: int,
    mode: str = "interval",
    scene_threshold: float = DEFAULT_SCENE_THRESHOLD,
) -> KeyframeResult:
    """Scene-aware extraction returning frame paths plus the detected cut count.

    hybrid merges uniform anchors with scene cuts; scene uses cuts only. Both
    stay inside the same max_frames budget and fall back to interval extraction
    when scene detection is unavailable or yields nothing usable.
    """
    _require("ffmpeg")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    probe = await probe_media(video_path)
    if not probe.has_video:
        return KeyframeResult([], 0)

    # Keep the real detected-cut count even if frame grabbing later falls back
    # to interval extraction, so the evidence still records what detection saw.
    detected_cuts = 0
    if mode in {"hybrid", "scene"} and probe.duration_ms and probe.duration_ms > 0:
        try:
            cuts = await detect_scene_cuts(video_path, threshold=scene_threshold)
        except MediaToolError:
            cuts = []
        detected_cuts = len(cuts)
        timestamps = plan_keyframe_timestamps(
            probe.duration_ms / 1000,
            max_frames,
            cuts,
            include_anchors=(mode == "hybrid"),
        )
        if timestamps:
            try:
                frames = await _extract_at_timestamps(
                    video_path, out, timestamps, width=width
                )
                if frames:
                    return KeyframeResult(frames, detected_cuts)
            except MediaToolError:
                pass

    frames = await _interval_keyframes(
        video_path, out, max_frames=max_frames, width=width, probe=probe
    )
    return KeyframeResult(frames, detected_cuts)


async def extract_keyframes(
    video_path: str | Path,
    out_dir: str | Path,
    *,
    max_frames: int,
    width: int,
    mode: str = "interval",
    scene_threshold: float = DEFAULT_SCENE_THRESHOLD,
) -> list[Path]:
    """Backward-compatible wrapper returning only the frame paths."""
    result = await extract_keyframes_detailed(
        video_path,
        out_dir,
        max_frames=max_frames,
        width=width,
        mode=mode,
        scene_threshold=scene_threshold,
    )
    return result.frames
