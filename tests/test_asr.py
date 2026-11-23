"""ASR backend tests: mocked HTTP transport and a stubbed local whisper."""

import asyncio
import sys
import types
from pathlib import Path

import httpx
import pytest

from mediavault.asr import (
    ASRError,
    ASRNotConfigured,
    LocalWhisperASR,
    OpenAICompatibleASR,
    build_asr_backend,
)
from mediavault.config import Config


def make_config(tmp_path: Path, **env) -> Config:
    payload = {
        "MEDIAVAULT_HOME": str(tmp_path / "vault"),
        "OPENAI_BASE_URL": "https://llm.example.test/v1",
        "OPENAI_API_KEY": "sk-test",
    }
    payload.update(env)
    return Config.load(payload)


def run(coro):
    return asyncio.run(coro)


def test_build_backend_off_returns_none(tmp_path):
    assert build_asr_backend(make_config(tmp_path, MEDIAVAULT_ASR_BACKEND="off")) is None


def test_build_backend_openai_without_key_returns_none(tmp_path):
    cfg = make_config(tmp_path, OPENAI_API_KEY="")
    assert build_asr_backend(cfg) is None


def test_build_backend_openai_with_key(tmp_path):
    backend = build_asr_backend(make_config(tmp_path))
    assert isinstance(backend, OpenAICompatibleASR)


def test_build_backend_local_class(tmp_path):
    backend = build_asr_backend(make_config(tmp_path, MEDIAVAULT_ASR_BACKEND="local"))
    assert isinstance(backend, LocalWhisperASR)


def test_build_backend_unknown_raises(tmp_path):
    with pytest.raises(ASRError, match="unknown MEDIAVAULT_ASR_BACKEND"):
        build_asr_backend(make_config(tmp_path, MEDIAVAULT_ASR_BACKEND="nope"))


def test_openai_transcription_multipart_and_verbose_json(tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFFfake")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["model"] = request.form.get("model") if hasattr(request, "form") else None
        body = request.content.decode("utf-8", errors="replace")
        seen["has_verbose_json"] = "verbose_json" in body
        seen["has_filename"] = "clip.wav" in body
        return httpx.Response(200, json={
            "text": "完整 转写 文本",
            "language": "zh",
            "segments": [
                {"start": 0.0, "end": 1.5, "text": "完整"},
                {"start": 1.5, "end": 3.0, "text": " 转写 文本 "},
                {"start": 3.0, "end": 4.0, "text": ""},
            ],
        })

    backend = OpenAICompatibleASR(
        make_config(tmp_path), transport=httpx.MockTransport(handler)
    )
    result = run(backend.transcribe(audio))

    assert result.backend == "openai-compatible"
    assert result.language == "zh"
    assert result.text == "完整 转写 文本"
    assert len(result.segments) == 2  # empty-text segment dropped
    assert result.segments[0].start_ms == 0
    assert result.segments[0].end_ms == 1500
    assert result.segments[1].text == "转写 文本"
    assert seen["url"] == "https://llm.example.test/v1/audio/transcriptions"
    assert seen["authorization"] == "Bearer sk-test"
    assert seen["has_verbose_json"] is True
    assert seen["has_filename"] is True


def test_openai_transcription_plain_text_payload(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")

    def handler(request):
        return httpx.Response(200, json={"text": "only text, no segments"})

    backend = OpenAICompatibleASR(
        make_config(tmp_path), transport=httpx.MockTransport(handler)
    )
    result = run(backend.transcribe(audio))
    assert result.segments == []
    assert result.text == "only text, no segments"


def test_openai_transcription_missing_key_raises(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    backend = OpenAICompatibleASR(make_config(tmp_path, OPENAI_API_KEY=""))
    with pytest.raises(ASRNotConfigured, match="OPENAI_API_KEY"):
        run(backend.transcribe(audio))


def test_openai_transcription_http_error(tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")

    def handler(request):
        return httpx.Response(413, json={"error": "file too large"})

    backend = OpenAICompatibleASR(
        make_config(tmp_path), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ASRError, match="413"):
        run(backend.transcribe(audio))


def test_local_whisper_missing_dependency(tmp_path, monkeypatch):
    # Ensure faster_whisper cannot be imported
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    backend = LocalWhisperASR(make_config(tmp_path))
    with pytest.raises(ASRNotConfigured, match="faster-whisper"):
        run(backend.transcribe(tmp_path / "a.wav"))


def test_local_whisper_normalizes_output(tmp_path, monkeypatch):
    class FakeSegment:
        def __init__(self, start, end, text):
            self.start = start
            self.end = end
            self.text = text

    class FakeInfo:
        language = "en"

    class FakeModel:
        def transcribe(self, path, vad_filter):
            return (
                iter([
                    FakeSegment(0.0, 1.0, " hello "),
                    FakeSegment(1.0, 2.0, "world"),
                ]),
                FakeInfo(),
            )

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = lambda model_name: FakeModel()
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)

    backend = LocalWhisperASR(
        make_config(tmp_path, MEDIAVAULT_WHISPER_MODEL="tiny")
    )
    result = run(backend.transcribe(tmp_path / "a.wav"))
    assert result.backend == "local-faster-whisper"
    assert result.language == "en"
    assert result.text == "hello world"
    assert result.segments[0].start_ms == 0 and result.segments[1].end_ms == 2000
