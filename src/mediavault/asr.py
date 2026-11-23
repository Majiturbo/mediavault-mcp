"""Speech-to-text backends behind one interface.

Two swappable backends:
  * OpenAICompatibleASR - POST /audio/transcriptions (multipart) on any
    OpenAI-shape endpoint (OpenAI, Volcengine Ark, local whisper servers...).
  * LocalWhisperASR     - faster-whisper, imported lazily so the heavy
    dependency stays optional.

The pipeline builder returns None when transcription is disabled or not
configured; the summarizer treats that as a skipped stage, not a failure.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from mediavault.config import Config


class ASRError(Exception):
    """Base class for transcription failures."""


class ASRNotConfigured(ASRError):
    """Raised when the selected backend cannot run (missing key/dependency)."""


@dataclass
class TranscriptSegment:
    start_ms: int | None
    end_ms: int | None
    text: str


@dataclass
class Transcription:
    text: str
    language: str | None
    segments: list[TranscriptSegment] = field(default_factory=list)
    backend: str = "unknown"


class ASRBackend(Protocol):
    name: str

    async def transcribe(self, audio_path: Path) -> Transcription:
        ...


class OpenAICompatibleASR:
    name = "openai-compatible"

    def __init__(
        self,
        config: Config,
        timeout: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._timeout = timeout
        self._transport = transport

    async def transcribe(self, audio_path: Path) -> Transcription:
        if not self._config.openai_api_key:
            raise ASRNotConfigured("OPENAI_API_KEY is not configured for transcription")

        url = self._config.openai_base_url.rstrip("/") + "/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self._config.openai_api_key}"}
        data = {
            "model": self._config.asr_model,
            "response_format": "verbose_json",
        }
        audio = Path(audio_path)
        files = {"file": (audio.name, audio.read_bytes(), "audio/wav")}

        async with httpx.AsyncClient(
            timeout=self._timeout,
            proxy=self._config.proxy,
            transport=self._transport,
        ) as client:
            response = await client.post(url, headers=headers, data=data, files=files)
            if response.status_code >= 400:
                raise ASRError(f"{response.status_code} {response.text[:400]}")
            payload = response.json()

        return self._normalize(payload)

    @staticmethod
    def _normalize(payload: dict) -> Transcription:
        text = payload.get("text", "")
        raw_segments = payload.get("segments") or []
        segments = [
            TranscriptSegment(
                start_ms=int(s["start"] * 1000) if s.get("start") is not None else None,
                end_ms=int(s["end"] * 1000) if s.get("end") is not None else None,
                text=s.get("text", "").strip(),
            )
            for s in raw_segments
            if s.get("text")
        ]
        return Transcription(
            text=text.strip(),
            language=payload.get("language"),
            segments=segments,
            backend="openai-compatible",
        )


class LocalWhisperASR:
    """faster-whisper backend; the package is imported only when selected."""

    name = "local-faster-whisper"

    def __init__(self, config: Config) -> None:
        self._config = config

    def _transcribe_blocking(self, audio_path: Path) -> Transcription:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ASRNotConfigured(
                "local ASR needs the optional dependency: pip install faster-whisper"
            ) from exc

        model = WhisperModel(self._config.whisper_model)
        segments_iter, info = model.transcribe(str(audio_path), vad_filter=True)
        segments = [
            TranscriptSegment(
                start_ms=int(seg.start * 1000),
                end_ms=int(seg.end * 1000),
                text=seg.text.strip(),
            )
            for seg in segments_iter
            if seg.text
        ]
        return Transcription(
            text=" ".join(s.text for s in segments),
            language=getattr(info, "language", None),
            segments=segments,
            backend=self.name,
        )

    async def transcribe(self, audio_path: Path) -> Transcription:
        return await asyncio.to_thread(self._transcribe_blocking, audio_path)


def build_asr_backend(config: Config) -> ASRBackend | None:
    """Resolve the configured backend; None means the ASR stage is skipped."""
    backend = config.asr_backend
    if backend == "off":
        return None
    if backend == "local":
        return LocalWhisperASR(config)
    if backend == "openai":
        if not config.openai_api_key:
            return None
        return OpenAICompatibleASR(config)
    raise ASRError(f"unknown MEDIAVAULT_ASR_BACKEND: {backend!r}")
