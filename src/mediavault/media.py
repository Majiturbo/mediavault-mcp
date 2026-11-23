"""ffmpeg/ffprobe wrappers: audio extraction and scene-spread keyframes.

Everything runs as an async subprocess (no new heavy Python dependency) and
produces ASR/VLM friendly artifacts: 16 kHz mono WAV for transcription and
downscaled JPEG frames spread evenly across the video for vision analysis.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path


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
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace")[-800:]
        raise MediaToolError(f"{cmd[0]} exited {proc.returncode}: {detail}")
    return stdout.decode("utf-8", errors="replace")


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


async def extract_keyframes(
    video_path: str | Path,
    out_dir: str | Path,
    *,
    max_frames: int,
    width: int,
) -> list[Path]:
    """Extract at most max_frames JPEGs evenly spread over the timeline.

    Uses the fps filter derived from duration so short and long videos both
    stay within budget; frames are downscaled to cap VLM token usage.
    """
    ffmpeg = _require("ffmpeg")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    probe = await probe_media(video_path)
    if not probe.has_video:
        return []

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
    extracted = sorted(out.glob("frame_*.jpg"))
    return [p for p in extracted if p.stat().st_size > 0]
