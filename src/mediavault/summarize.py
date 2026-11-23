"""Multimodal summarization pipeline.

Three evidence channels are fused into one stable JSON schema:
  1. platform caption / title / description (always available)
  2. speech transcript  (ffmpeg audio extract -> ASR backend)
  3. visual facts       (ffmpeg keyframes     -> VLM analyzer)

Every media stage is independently fault-tolerant: a missing audio stream,
a disabled backend or a failed VLM call is recorded honestly in the pipeline
block instead of failing the whole summary. With no local video file the
summarizer degrades to the M0 caption-only behavior.

Stable output shape:
  tldr, chapters, transcript, visual_facts, entities, hooks, language,
  pipeline {source, transcript, keyframes, errors, canonical_id}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from mediavault import media
from mediavault.asr import ASRBackend, ASRError, Transcription
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
    errors: list[str] = field(default_factory=list)


class Summarizer:
    def __init__(
        self,
        llm: OpenAICompatibleClient,
        asr_backend: ASRBackend | None = None,
        vision: VisionAnalyzer | None = None,
        config=None,
    ) -> None:
        self._llm = llm
        self._asr = asr_backend
        self._vision = vision
        self._config = config

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
    # Stage 2: keyframe vision analysis
    # ------------------------------------------------------------------ #
    async def _run_vision(
        self, video_path: Path, status: PipelineStatus
    ) -> list[VisualFact]:
        if self._vision is None or not await self._vision.available():
            status.keyframes = "not-configured"
            return []
        try:
            frames = await media.extract_keyframes(
                video_path,
                video_path.parent / "keyframes",
                max_frames=self._config.keyframe_max if self._config else 8,
                width=self._config.keyframe_width if self._config else 768,
            )
            if not frames:
                status.keyframes = "skipped-no-video"
                return []
            facts = await self._vision.describe_frames(frames)
            status.keyframes = "ok" if facts else "empty"
            return facts
        except (media.MediaToolError, LLMError) as exc:
            status.keyframes = "error"
            status.errors.append(f"vision: {type(exc).__name__}: {exc}"[:300])
            return []

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
        transcription: Transcription | None, facts: list[VisualFact]
    ) -> str:
        has_audio = transcription is not None and bool(transcription.text)
        has_vision = bool(facts)
        if has_audio and has_vision:
            return "multimodal"
        if has_audio:
            return "transcript+caption"
        if has_vision:
            return "vision+caption"
        return "caption-only"

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    async def summarize(
        self, resource: Resource, video_path: str | Path | None = None
    ) -> dict:
        status = PipelineStatus()
        transcription: Transcription | None = None
        facts: list[VisualFact] = []

        if video_path is not None:
            path = Path(video_path)
            transcription = await self._run_asr(path, status)
            facts = await self._run_vision(path, status)

        status.source = self._derive_source(transcription, facts)

        raw = await self._llm.chat(
            [
                ChatMessage(
                    role="user",
                    content=self._build_prompt(resource, transcription, facts),
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

        structured["transcript"] = transcription.text if transcription else ""
        structured["visual_facts"] = [
            {"frame": f.frame, "index": f.index, "observation": f.observation}
            for f in facts
        ]
        structured["pipeline"] = {
            "source": status.source,
            "transcript": status.transcript,
            "keyframes": status.keyframes,
            "errors": status.errors,
            "canonical_id": resource.canonical_id,
        }
        return structured


def _format_ms(value: int | None) -> str:
    if value is None:
        return "??:??:??"
    seconds, ms = divmod(max(0, value), 1000)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
