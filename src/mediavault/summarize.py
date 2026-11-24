"""Multimodal evidence collection and optional LLM fusion.

Two layers, deliberately separable so an MCP host that already runs an LLM
does not need a second model or API key:

  collect_evidence()  - deterministic and key-free. Extracts a 16 kHz audio
    track and timeline-spread keyframes with ffmpeg, transcribes speech via
    the configured ASR backend (local faster-whisper needs no key), and only
    runs a VLM over the frames when one is configured. Returns an
    EvidenceBundle the host agent can reason over directly (frames are also
    attached as MCP image blocks by the server).

  summarize()         - optional convenience for clients without their own
    model: collect_evidence() then one OpenAI-compatible chat call that fuses
    everything into the stable structured summary schema.

Every media stage is independently fault-tolerant: a missing audio stream,
a disabled backend or a failed VLM call is recorded honestly in the pipeline
block instead of failing the whole run.

Stable summary shape:
  tldr, chapters, transcript, visual_facts, entities, hooks, language,
  pipeline {source, transcript, keyframes, errors, canonical_id, frames_extracted}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from mediavault import media
from mediavault.asr import ASRBackend, ASRError, Transcription
from mediavault.diarize import DiarizationError, DiarizationTurn, Diarizer
from mediavault.llm import ChatMessage, LLMError, OpenAICompatibleClient
from mediavault.models import Resource
from mediavault.vision import VisionAnalyzer, VisualFact

_CAPTION_LIMIT = 8000
_TRANSCRIPT_LIMIT = 12000

_SUMMARY_PROMPT = """You are analyzing one social media video. Return STRICT JSON
with these keys:
  tldr: string, 1-3 sentences in the video's language
  chapters: array of {start_ms, end_ms, topic} aligned to the transcript
    timestamps when available; empty array when no timeline exists
  entities: object with arrays products, locations, people
  hooks: array of strings, opening hooks / CTAs worth reusing
  language: ISO code of the content
Base the answer ONLY on the evidence below. Do not invent facts, and prefer
the transcript and visual observations over the caption when they conflict.

Title: __TITLE__
Author: __AUTHOR__
Caption / description:
__CAPTION__

Speech transcript (with timestamps):
__TRANSCRIPT__

Visual observations from sampled keyframes:
__VISUAL__
"""


@dataclass
class PipelineStatus:
    source: str = "caption-only"
    transcript: str = "off"
    keyframes: str = "off"
    frames_extracted: int = 0
    # M3: scene-aware extraction and speaker turn labels.
    keyframe_mode: str = "interval"
    scene_cuts: int = 0
    diarization: str = "off"
    errors: list[str] = field(default_factory=list)


@dataclass
class EvidenceBundle:
    resource: Resource
    video_path: Path | None
    transcription: Transcription | None
    frame_paths: list[Path]
    visual_facts: list[VisualFact]
    status: PipelineStatus
    speaker_turns: list[DiarizationTurn] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Key-free evidence payload for the host agent's own LLM."""
        transcription = self.transcription
        raw_segments = transcription.segments if transcription else []
        speaker_by_index = {i: t.speaker for i, t in enumerate(self.speaker_turns)}
        return {
            "canonical_id": self.resource.canonical_id,
            "platform": self.resource.platform,
            "title": self.resource.title,
            "author": self.resource.author.name,
            "caption": self.resource.caption,
            "page_url": self.resource.page_url,
            "transcript": transcription.text if transcription else "",
            "transcript_segments": [
                {
                    "start_ms": s.start_ms,
                    "end_ms": s.end_ms,
                    "text": s.text,
                    **(
                        {"speaker": speaker_by_index[i]}
                        if i in speaker_by_index
                        else {}
                    ),
                }
                for i, s in enumerate(raw_segments)
            ],
            "speaker_turns": [
                {"speaker": t.speaker, "start_ms": t.start_ms, "end_ms": t.end_ms}
                for t in self.speaker_turns
            ],
            "frames_extracted": len(self.frame_paths),
            "scene_cuts": self.status.scene_cuts,
            "keyframe_mode": self.status.keyframe_mode,
            "frame_paths": [str(p) for p in self.frame_paths],
            "visual_facts": [
                {"frame": f.frame, "index": f.index, "observation": f.observation}
                for f in self.visual_facts
            ],
            "pipeline": {
                "source": self.status.source,
                "transcript": self.status.transcript,
                "keyframes": self.status.keyframes,
                "frames_extracted": self.status.frames_extracted,
                "keyframe_mode": self.status.keyframe_mode,
                "scene_cuts": self.status.scene_cuts,
                "diarization": self.status.diarization,
                "errors": self.status.errors,
                "canonical_id": self.resource.canonical_id,
            },
        }


class Summarizer:
    def __init__(
        self,
        llm: OpenAICompatibleClient | None = None,
        asr_backend: ASRBackend | None = None,
        vision: VisionAnalyzer | None = None,
        config=None,
        diarizer: Diarizer | None = None,
    ) -> None:
        self._llm = llm
        self._asr = asr_backend
        self._vision = vision
        self._config = config
        self._diarizer = diarizer

    # ------------------------------------------------------------------ #
    # Stage 1: speech transcription
    # ------------------------------------------------------------------ #
    async def _run_asr(
        self, video_path: Path, status: PipelineStatus
    ) -> Transcription | None:
        if self._asr is None:
            status.transcript = "not-configured"
            return None
        try:
            probe = await media.probe_media(video_path)
            if not probe.has_audio:
                status.transcript = "skipped-no-audio"
                return None
            wav = await media.extract_audio(video_path, video_path.parent / "audio.wav")
            transcription = await self._asr.transcribe(wav)
            if not transcription.text:
                status.transcript = "empty"
                return None
            status.transcript = "ok"
            return transcription
        except (media.MediaToolError, ASRError) as exc:
            status.transcript = "error"
            status.errors.append(f"asr: {type(exc).__name__}: {exc}"[:300])
            return None

    # ------------------------------------------------------------------ #
    # Stage 1b: optional speaker turn labels (dependency-free heuristic)
    # ------------------------------------------------------------------ #
    def _run_diarization(
        self, transcription: Transcription | None, status: PipelineStatus
    ) -> list[DiarizationTurn]:
        if self._diarizer is None:
            status.diarization = "off"
            return []
        if transcription is None or not transcription.segments:
            status.diarization = "skipped-no-transcript"
            return []
        try:
            turns = self._diarizer.diarize(transcription.segments)
        except DiarizationError as exc:
            status.diarization = "error"
            status.errors.append(f"diarization: {type(exc).__name__}: {exc}"[:300])
            return []
        status.diarization = self._diarizer.name
        return turns

    # ------------------------------------------------------------------ #
    # Stage 2: local keyframe extraction (no key) + optional VLM describe
    # ------------------------------------------------------------------ #
    async def _extract_frames(
        self, video_path: Path, status: PipelineStatus
    ) -> list[Path]:
        max_frames = getattr(self._config, "keyframe_max", 8) if self._config else 8
        width = getattr(self._config, "keyframe_width", 768) if self._config else 768
        mode = getattr(self._config, "keyframe_mode", "interval") if self._config else "interval"
        threshold = (
            getattr(self._config, "scene_threshold", media.DEFAULT_SCENE_THRESHOLD)
            if self._config
            else media.DEFAULT_SCENE_THRESHOLD
        )
        status.keyframe_mode = mode
        try:
            if mode in {"hybrid", "scene"}:
                result = await media.extract_keyframes_detailed(
                    video_path,
                    video_path.parent / "keyframes",
                    max_frames=max_frames,
                    width=width,
                    mode=mode,
                    scene_threshold=threshold,
                )
                status.scene_cuts = result.scene_cuts
                frames = result.frames
            else:
                frames = await media.extract_keyframes(
                    video_path,
                    video_path.parent / "keyframes",
                    max_frames=max_frames,
                    width=width,
                )
            status.frames_extracted = len(frames)
            return frames
        except media.MediaToolError as exc:
            status.keyframes = "error"
            status.errors.append(f"keyframes: {type(exc).__name__}: {exc}"[:300])
            return []

    async def _describe_frames(
        self, frames: list[Path], status: PipelineStatus
    ) -> list[VisualFact]:
        if self._vision is None or not await self._vision.available():
            # Frames may still be on disk; the host agent receives them as
            # image blocks and does its own vision reasoning. Keep any error
            # marker already recorded by the extraction stage.
            if status.keyframes != "error":
                status.keyframes = "extracted" if frames else "not-configured"
            return []
        if not frames:
            if status.keyframes != "error":
                status.keyframes = "skipped-no-video"
            return []
        try:
            facts = await self._vision.describe_frames(frames)
            status.keyframes = "ok" if facts else "empty"
            return facts
        except (LLMError, media.MediaToolError) as exc:
            status.keyframes = "error"
            status.errors.append(f"vision: {type(exc).__name__}: {exc}"[:300])
            return []

    # ------------------------------------------------------------------ #
    # Key-free evidence collection
    # ------------------------------------------------------------------ #
    async def collect_evidence(
        self, resource: Resource, video_path: str | Path | None = None
    ) -> EvidenceBundle:
        status = PipelineStatus()
        transcription: Transcription | None = None
        frames: list[Path] = []

        facts: list[VisualFact] = []
        speaker_turns: list[DiarizationTurn] = []
        if video_path is not None:
            path = Path(video_path)
            transcription = await self._run_asr(path, status)
            speaker_turns = self._run_diarization(transcription, status)
            frames = await self._extract_frames(path, status)
            facts = await self._describe_frames(frames, status)

        status.source = self._derive_source(transcription, facts, frames)
        return EvidenceBundle(
            resource=resource,
            video_path=Path(video_path) if video_path is not None else None,
            transcription=transcription,
            frame_paths=frames,
            visual_facts=facts,
            status=status,
            speaker_turns=speaker_turns,
        )

    # ------------------------------------------------------------------ #
    # Prompt construction (token replacement, never %/format: captions
    # may contain '%' or braces).
    # ------------------------------------------------------------------ #
    @staticmethod
    def _format_transcript(transcription: Transcription | None) -> str:
        if transcription is None:
            return "(none)"
        if not transcription.segments:
            return transcription.text[:_TRANSCRIPT_LIMIT] or "(empty)"
        lines = []
        for seg in transcription.segments:
            ts = _format_ms(seg.start_ms)
            lines.append(f"[{ts}] {seg.text}")
        joined = "\n".join(lines)
        return joined[:_TRANSCRIPT_LIMIT] or "(empty)"

    @staticmethod
    def _format_visual(facts: list[VisualFact]) -> str:
        if not facts:
            return "(none)"
        return "\n".join(f"- {f.frame}: {f.observation}" for f in facts)

    def _build_prompt(
        self,
        resource: Resource,
        transcription: Transcription | None,
        facts: list[VisualFact],
    ) -> str:
        return (
            _SUMMARY_PROMPT
            .replace("__TITLE__", resource.title or "")
            .replace("__AUTHOR__", resource.author.name or "")
            .replace("__CAPTION__", (resource.caption or "")[:_CAPTION_LIMIT])
            .replace("__TRANSCRIPT__", self._format_transcript(transcription))
            .replace("__VISUAL__", self._format_visual(facts))
        )

    @staticmethod
    def _derive_source(
        transcription: Transcription | None,
        facts: list[VisualFact],
        frames: list[Path],
    ) -> str:
        has_audio = transcription is not None and bool(transcription.text)
        has_vision = bool(facts)
        has_frames = bool(frames)
        if has_audio and has_vision:
            return "multimodal"
        if has_audio:
            return "transcript+caption"
        if has_vision:
            return "vision+caption"
        if has_frames:
            # Frames available for the host agent even without a server VLM.
            return "frames+caption"
        return "caption-only"

    # ------------------------------------------------------------------ #
    # Optional server-side fusion (requires a configured LLM endpoint)
    # ------------------------------------------------------------------ #
    async def summarize(
        self, resource: Resource, video_path: str | Path | None = None
    ) -> dict:
        if self._llm is None:
            raise LLMError(
                "no LLM endpoint configured; use collect_evidence() and let the "
                "host agent reason over the returned evidence instead"
            )
        bundle = await self.collect_evidence(resource, video_path)
        raw = await self._llm.chat(
            [
                ChatMessage(
                    role="user",
                    content=self._build_prompt(
                        resource, bundle.transcription, bundle.visual_facts
                    ),
                )
            ],
            json_mode=True,
        )
        try:
            structured = json.loads(raw)
            if not isinstance(structured, dict):
                raise ValueError("LLM returned a non-object JSON value")
        except (json.JSONDecodeError, ValueError):
            # Some compatible endpoints ignore response_format; keep the text.
            structured = {
                "tldr": raw,
                "chapters": [],
                "entities": {},
                "hooks": [],
                "language": "unknown",
            }

        transcription = bundle.transcription
        structured["transcript"] = transcription.text if transcription else ""
        structured["visual_facts"] = [
            {"frame": f.frame, "index": f.index, "observation": f.observation}
            for f in bundle.visual_facts
        ]
        structured["pipeline"] = {
            "source": bundle.status.source,
            "transcript": bundle.status.transcript,
            "keyframes": bundle.status.keyframes,
            "frames_extracted": bundle.status.frames_extracted,
            "keyframe_mode": bundle.status.keyframe_mode,
            "scene_cuts": bundle.status.scene_cuts,
            "diarization": bundle.status.diarization,
            "errors": bundle.status.errors,
            "canonical_id": resource.canonical_id,
        }
        structured["speaker_turns"] = [
            {"speaker": t.speaker, "start_ms": t.start_ms, "end_ms": t.end_ms}
            for t in bundle.speaker_turns
        ]
        return structured


def _format_ms(value: int | None) -> str:
    if value is None:
        return "??:??:??"
    seconds, ms = divmod(max(0, value), 1000)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
