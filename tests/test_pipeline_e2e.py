"""Offline end-to-end pipeline test.

A real synthetic video (ffmpeg lavfi) flows through the full service path:
registry probe -> speed download -> real audio extraction + keyframe
sampling -> stubbed ASR/VLM backends -> fusion LLM. Only the model HTTP
calls are faked; every local media step is genuine. Skipped without ffmpeg.
"""

import asyncio
import shutil
from pathlib import Path

import pytest

from mediavault import media
from mediavault import service as service_module
from mediavault.adapters.base import AdapterError
from mediavault.asr import Transcription, TranscriptSegment
from mediavault.config import Config
from mediavault.models import Author, Resource
from mediavault.summarize import Summarizer
from mediavault.vision import VisualFact

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


class StubLLM:
    async def chat(self, messages, *, model=None, temperature=0.3, json_mode=False):
        return (
            '{"tldr": "端到端融合总结", "chapters": ['
            '{"start_ms": 0, "end_ms": 2000, "topic": "合成片段"}],'
            ' "entities": {"products": [], "locations": [], "people": []},'
            ' "hooks": ["开场钩子"], "language": "zh"}'
        )


class StubASR:
    name = "stub-asr"

    async def transcribe(self, audio_path: Path) -> Transcription:
        assert audio_path.is_file() and audio_path.stat().st_size > 0
        return Transcription(
            text="合成语音转写",
            language="zh",
            segments=[TranscriptSegment(0, 2000, "合成语音转写")],
        )


class StubVision:
    async def available(self) -> bool:
        return True

    async def describe_frames(self, frames: list[Path]) -> list[VisualFact]:
        assert frames and all(p.is_file() for p in frames)
        return [VisualFact(p.name, i + 1, f"画面观察 {i + 1}") for i, p in enumerate(frames)]


class StubAdapter:
    name = "stub-adapter"

    def __init__(self, video_path: Path):
        self._video = video_path
        self.qualities: list[str] = []

    async def download(self, resource, quality="compat"):
        self.qualities.append(quality)
        return [str(self._video)]


class StubRegistry:
    def __init__(self, resource: Resource, adapter: StubAdapter):
        self._resource = resource
        self.adapter = adapter

    async def probe(self, url):
        return self._resource

    def chain_for(self, url):
        return [self.adapter]


async def _make_clip(path: Path) -> None:
    cmd = [
        shutil.which("ffmpeg"), "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=640x360:rate=15",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-shortest", str(path),
    ]
    proc = await media.asyncio.create_subprocess_exec(
        *cmd, stdout=media.asyncio.subprocess.PIPE, stderr=media.asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode("utf-8", errors="replace")


async def _wait_job(svc, job_id):
    for _ in range(100):
        job = svc.jobs.get(job_id)
        if job.status in {"succeeded", "failed"}:
            return job
        await asyncio.sleep(0.02)
    return svc.jobs.get(job_id)


@requires_ffmpeg
def test_full_service_pipeline_with_real_ffmpeg(tmp_path, monkeypatch):
    home = tmp_path / "vault"
    clip = tmp_path / "clip.mp4"
    asyncio.run(_make_clip(clip))

    resource = Resource(
        canonical_id="douyin:e2e1",
        platform="douyin",
        content_type="video",
        page_url="https://v.douyin.com/e2e/",
        title="端到端测试",
        author=Author(name="测试博主"),
        caption="真实 ffmpeg 管线验证 100% {ok}",
        adapter_used="stub-adapter",
    )
    adapter = StubAdapter(clip)
    registry = StubRegistry(resource, adapter)
    monkeypatch.setattr(service_module, "build_default_registry", lambda cfg: registry)

    config = Config.load({
        "MEDIAVAULT_HOME": str(home),
        "OPENAI_API_KEY": "sk-stub",
        "MEDIAVAULT_KEYFRAME_MAX": "3",
        "MEDIAVAULT_KEYFRAME_WIDTH": "320",
    })
    svc = service_module.MediaVaultService(config=config)
    svc._summarizer = Summarizer(StubLLM(), StubASR(), StubVision(), config)

    job_id = asyncio.run(svc.summarize("看看 https://v.douyin.com/e2e/"))
    job = asyncio.run(_wait_job(svc, job_id))

    assert job.status == "succeeded", job.error
    assert adapter.qualities == ["speed"]  # summarization uses the light profile
    summary = job.result["summary"]
    assert summary["tldr"] == "端到端融合总结"
    assert summary["transcript"] == "合成语音转写"
    assert summary["visual_facts"], "real keyframes should produce visual facts"
    pipeline = summary["pipeline"]
    assert pipeline == {
        "source": "multimodal",
        "transcript": "ok",
        "keyframes": "ok",
        "frames_extracted": 3,
        "keyframe_mode": "interval",
        "scene_cuts": 0,
        "diarization": "off",
        "errors": [],
        "canonical_id": "douyin:e2e1",
    }

    # Genuine ffmpeg artifacts were produced next to the source clip.
    assert (clip.parent / "audio.wav").is_file()
    frames_dir = clip.parent / "keyframes"
    assert frames_dir.is_dir() and len(list(frames_dir.glob("frame_*.jpg"))) >= 1


@requires_ffmpeg
def test_pipeline_degrades_when_adapter_download_fails(tmp_path, monkeypatch):
    home = tmp_path / "vault"

    class FailingAdapter(StubAdapter):
        async def download(self, resource, quality="compat"):
            raise AdapterError("network blocked")

    resource = Resource(
        canonical_id="douyin:e2e2", platform="douyin", content_type="video",
        page_url="https://v.douyin.com/x/", title="t", author=Author(name="a"),
        caption="c", adapter_used="stub-adapter",
    )
    registry = StubRegistry(resource, FailingAdapter(tmp_path / "missing.mp4"))
    monkeypatch.setattr(service_module, "build_default_registry", lambda cfg: registry)

    config = Config.load({"MEDIAVAULT_HOME": str(home), "OPENAI_API_KEY": "sk-stub"})
    svc = service_module.MediaVaultService(config=config)
    svc._summarizer = Summarizer(StubLLM(), StubASR(), StubVision(), config)

    job_id = asyncio.run(svc.summarize("https://v.douyin.com/x/"))
    job = asyncio.run(_wait_job(svc, job_id))

    assert job.status == "succeeded"  # caption-only fallback, not a failed job
    pipeline = job.result["summary"]["pipeline"]
    assert pipeline["source"] == "caption-only"
    assert pipeline["transcript"] == "skipped-no-local-file"
    assert any("download:AdapterError" in e for e in pipeline["errors"])
