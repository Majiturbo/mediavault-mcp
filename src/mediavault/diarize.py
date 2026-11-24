"""Speaker diarization behind one optional, dependency-free interface.

True voiceprint diarization (e.g. pyannote) needs gated model downloads and a
Hugging Face token, which conflicts with this project's key-free, offline
default. We therefore ship an honest, zero-dependency turn-taking heuristic
and keep a narrow Diarizer protocol so a real embedding-based backend can be
slotted in later without touching the pipeline.

PauseGapDiarizer labels transcript segments as SPEAKER_A / SPEAKER_B by
flipping the active speaker whenever the silence between two consecutive
segments exceeds a configurable pause. These are conversational turn labels,
not identified people, and the pipeline marker says so explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mediavault.asr import TranscriptSegment
from mediavault.config import Config


class DiarizationError(Exception):
    """Base class for diarization failures."""


@dataclass(frozen=True)
class DiarizationTurn:
    speaker: str
    start_ms: int | None
    end_ms: int | None


class Diarizer(Protocol):
    name: str

    def diarize(self, segments: list[TranscriptSegment]) -> list[DiarizationTurn]:
        ...


class PauseGapDiarizer:
    """Assign A/B turn labels from inter-segment pauses (no model download)."""

    name = "pause-gap-heuristic"

    def __init__(self, gap_ms: int = 700, max_speakers: int = 2) -> None:
        self.gap_ms = max(1, int(gap_ms))
        self.max_speakers = max(1, int(max_speakers))

    def diarize(self, segments: list[TranscriptSegment]) -> list[DiarizationTurn]:
        turns: list[DiarizationTurn] = []
        active = 0
        prev_end: int | None = None
        for seg in segments:
            if (
                prev_end is not None
                and seg.start_ms is not None
                and seg.start_ms - prev_end >= self.gap_ms
            ):
                active = (active + 1) % self.max_speakers
            speaker = f"SPEAKER_{chr(65 + active)}"
            turns.append(DiarizationTurn(speaker, seg.start_ms, seg.end_ms))
            if seg.end_ms is not None:
                prev_end = seg.end_ms
        return turns


def build_diarizer(config: Config) -> Diarizer | None:
    """Resolve the diarization backend; None means the stage stays off."""
    mode = config.diarization
    if mode == "off":
        return None
    if mode == "heuristic":
        return PauseGapDiarizer(config.diarize_gap_ms)
    raise DiarizationError(f"unknown MEDIAVAULT_DIARIZATION: {mode!r}")
