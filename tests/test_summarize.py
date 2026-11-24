"""Summarizer pipeline tests: caption-only, ASR, vision and all fallbacks."""

import asyncio
from pathlib import Path

import pytest

from mediavault import media
from mediavault.asr import ASRError, Transcription, TranscriptSegment
from mediavault.diarize import DiarizationError, PauseGapDiarizer
from mediavault.llm import LLMError
from mediavault.models import Author, Resource
from mediavault.summarize import Summarizer, _format_ms
from mediavault.vision import VisualFact


class FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict] = []

    async def chat(self, messages, *, model=None, temperature=0.3, json_mode=False):
        self.calls.append({"messages": messages, "json_mode": json_mode, "model": model})
        return self.response


class FakeASR:
    name = "fake-asr"

    def __init__(self, transcription: Transcription | None = None, fail: bool = False):
        self._transcription = transcription or Transcription(
            text="完整 转写", language="zh",
            segments=[
                TranscriptSegment(0, 1500, "完整"),
                TranscriptSegment(1500, 3000, "转写"),
            ],
        )
        self.fail = fail
        self.audio_inputs: list[Path] = []

    async def transcribe(self, audio_path: Path) -> Transcription:
        self.audio_inputs.append(audio_path)
        if self.fail:
            raise ASRError("asr backend 500")
        return self._transcription


class FakeVision:
    def __init__(self, facts: list[VisualFact] | None = None, available: bool = True,
                 fail: bool = False):
        self._facts = facts if facts is not None else [
            VisualFact("frame_001.jpg", 1, "片头标题"),
            VisualFact("frame_002.jpg", 2, "产品特写"),
        ]
        self._available = available
        self.fail = fail
        self.frame_inputs: list[Path] = []

    async def available(self) -> bool:
        return self._available

    async def describe_frames(self, frames: list[Path]) -> list[VisualFact]:
        self.frame_inputs.extend(frames)
        if self.fail:
            raise media.MediaToolError("ffmpeg exited 1")
        return self._facts


def resource(**overrides):
    base = dict(
        canonical_id="douyin:1",
        platform="douyin",
        content_type="video",
        page_url="https://v.douyin.com/x/",
        title="标题",
        author=Author(name="博主"),
        caption="这是一条带百分号 100% 和大括号 {json} 的文案",
    )
    base.update(overrides)
    return Resource(**base)


def run(coro):
    return asyncio.run(coro)


class FakeConfig:
    keyframe_max = 6
    keyframe_width = 640


def patch_media(monkeypatch, *, has_audio=True, has_video=True, duration_ms=4000):
    async def fake_probe(path):
        return media.MediaProbe(duration_ms, has_audio, has_video)

    async def fake_audio(video, out):
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"RIFF")
        return out

    async def fake_frames(video, out_dir, *, max_frames, width):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        made = []
        for i in range(1, 3):
            p = out_dir / f"frame_{i:03d}.jpg"
            p.write_bytes(b"jpeg")
            made.append(p)
        return made

    monkeypatch.setattr(media, "probe_media", fake_probe)
    monkeypatch.setattr(media, "extract_audio", fake_audio)
    monkeypatch.setattr(media, "extract_keyframes", fake_frames)


# --------------------------------------------------------------------------- #
# Caption-only degradation (M0 behavior, no local file)
# --------------------------------------------------------------------------- #
def test_caption_only_pipeline_markers():
    fake = FakeLLM(
        '{"tldr": "要点", "chapters": [], "entities": {"products": []},'
        ' "hooks": ["开场"], "language": "zh"}'
    )
    result = run(Summarizer(fake).summarize(resource()))

    assert result["tldr"] == "要点"
    assert result["transcript"] == ""
    assert result["visual_facts"] == []
    assert result["pipeline"] == {
        "source": "caption-only",
        "transcript": "off",
        "keyframes": "off",
        "frames_extracted": 0,
        "keyframe_mode": "interval",
        "scene_cuts": 0,
        "diarization": "off",
        "errors": [],
        "canonical_id": "douyin:1",
    }
    assert fake.calls[0]["json_mode"] is True


def test_prompt_survives_percent_and_braces():
    fake = FakeLLM("{}")
    result = run(Summarizer(fake).summarize(resource()))
    prompt = fake.calls[0]["messages"][0].content
    assert "100%" in prompt
    assert "{json}" in prompt
    assert "博主" in prompt
    assert result["pipeline"]["canonical_id"] == "douyin:1"


def test_non_json_response_is_wrapped_not_crashed():
    fake = FakeLLM("模型自由发挥的纯文本总结")
    result = run(Summarizer(fake).summarize(resource()))
    assert result["tldr"] == "模型自由发挥的纯文本总结"
    assert result["chapters"] == []
    assert result["language"] == "unknown"


def test_json_array_response_is_wrapped_not_crashed():
    fake = FakeLLM('["not", "an", "object"]')
    result = run(Summarizer(fake).summarize(resource()))
    assert result["tldr"] == '["not", "an", "object"]'
    assert result["language"] == "unknown"


def test_long_caption_is_truncated():
    fake = FakeLLM("{}")
    run(Summarizer(fake).summarize(resource(caption="字" * 20000)))
    prompt = fake.calls[0]["messages"][0].content
    assert prompt.count("字") == 8000


def test_missing_caption_still_works():
    fake = FakeLLM("{}")
    result = run(Summarizer(fake).summarize(resource(caption=None)))
    assert result["pipeline"]["source"] == "caption-only"


# --------------------------------------------------------------------------- #
# Full multimodal pipeline
# --------------------------------------------------------------------------- #
def test_full_multimodal_pipeline(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    llm = FakeLLM('{"tldr": "融合总结", "chapters": [], "entities": {},'
                  ' "hooks": [], "language": "zh"}')
    asr = FakeASR()
    vision = FakeVision()
    summarizer = Summarizer(llm, asr, vision, FakeConfig())

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    result = run(summarizer.summarize(resource(), video_path=video))

    assert result["pipeline"]["source"] == "multimodal"
    assert result["pipeline"]["transcript"] == "ok"
    assert result["pipeline"]["keyframes"] == "ok"
    assert result["pipeline"]["errors"] == []
    assert result["transcript"] == "完整 转写"
    assert result["visual_facts"][0]["observation"] == "片头标题"

    prompt = llm.calls[0]["messages"][0].content
    assert "[00:00:00] 完整" in prompt
    assert "[00:00:01] 转写" in prompt
    assert "frame_001.jpg: 片头标题" in prompt
    assert asr.audio_inputs[0].name == "audio.wav"
    assert len(vision.frame_inputs) == 2


def test_asr_failure_does_not_break_pipeline(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    llm = FakeLLM("{}")
    asr = FakeASR(fail=True)
    vision = FakeVision()
    summarizer = Summarizer(llm, asr, vision, FakeConfig())

    result = run(summarizer.summarize(resource(), video_path=tmp_path / "clip.mp4"))

    assert result["pipeline"]["transcript"] == "error"
    assert result["pipeline"]["keyframes"] == "ok"
    assert result["pipeline"]["source"] == "vision+caption"
    assert any("asr: ASRError" in e for e in result["pipeline"]["errors"])
    # LLM still called with the visual evidence
    assert "frame_001.jpg" in llm.calls[0]["messages"][0].content


def test_no_audio_stream_skips_asr(monkeypatch, tmp_path):
    patch_media(monkeypatch, has_audio=False)
    llm = FakeLLM("{}")
    asr = FakeASR()
    vision = FakeVision()
    summarizer = Summarizer(llm, asr, vision, FakeConfig())

    result = run(summarizer.summarize(resource(), video_path=tmp_path / "clip.mp4"))

    assert result["pipeline"]["transcript"] == "skipped-no-audio"
    assert asr.audio_inputs == []
    assert result["pipeline"]["source"] == "vision+caption"


def test_without_asr_backend_marks_not_configured(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    llm = FakeLLM("{}")
    vision = FakeVision()
    summarizer = Summarizer(llm, None, vision, FakeConfig())

    result = run(summarizer.summarize(resource(), video_path=tmp_path / "clip.mp4"))

    assert result["pipeline"]["transcript"] == "not-configured"
    assert result["pipeline"]["keyframes"] == "ok"


def test_frames_extracted_for_host_when_vlm_unavailable(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    llm = FakeLLM("{}")
    asr = FakeASR()
    vision = FakeVision(available=False)
    summarizer = Summarizer(llm, asr, vision, FakeConfig())

    result = run(summarizer.summarize(resource(), video_path=tmp_path / "clip.mp4"))

    # No server VLM, but ffmpeg frames are still produced for the host agent.
    assert result["pipeline"]["keyframes"] == "extracted"
    assert result["pipeline"]["frames_extracted"] == 2
    assert vision.frame_inputs == []
    assert result["pipeline"]["source"] == "transcript+caption"


def test_no_vision_analyzer_still_extracts_frames(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    llm = FakeLLM("{}")
    summarizer = Summarizer(llm, FakeASR(), None, FakeConfig())

    result = run(summarizer.summarize(resource(), video_path=tmp_path / "clip.mp4"))
    assert result["pipeline"]["keyframes"] == "extracted"
    assert result["pipeline"]["frames_extracted"] == 2


def test_keyframe_extraction_failure_is_isolated(monkeypatch, tmp_path):
    patch_media(monkeypatch)

    async def boom(video, out_dir, *, max_frames, width):
        raise media.MediaToolError("ffmpeg exited 1")

    monkeypatch.setattr(media, "extract_keyframes", boom)
    llm = FakeLLM("{}")
    asr = FakeASR()
    vision = FakeVision()
    summarizer = Summarizer(llm, asr, vision, FakeConfig())

    result = run(summarizer.summarize(resource(), video_path=tmp_path / "clip.mp4"))

    assert result["pipeline"]["keyframes"] == "error"
    assert result["pipeline"]["transcript"] == "ok"
    assert any("keyframes: MediaToolError" in e for e in result["pipeline"]["errors"])
    assert result["pipeline"]["source"] == "transcript+caption"


def test_vlm_call_failure_is_isolated(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    llm = FakeLLM("{}")
    asr = FakeASR()
    vision = FakeVision(fail=True)
    summarizer = Summarizer(llm, asr, vision, FakeConfig())

    result = run(summarizer.summarize(resource(), video_path=tmp_path / "clip.mp4"))

    assert result["pipeline"]["keyframes"] == "error"
    assert result["pipeline"]["transcript"] == "ok"
    assert any("vision: MediaToolError" in e for e in result["pipeline"]["errors"])
    # Fusion LLM still runs on caption + transcript evidence.
    assert llm.calls[0]["messages"][0].content is not None


def test_zero_keyframes_skips_vision_stage(monkeypatch, tmp_path):
    patch_media(monkeypatch)

    async def no_frames(video, out_dir, *, max_frames, width):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        return []

    monkeypatch.setattr(media, "extract_keyframes", no_frames)
    llm = FakeLLM("{}")
    asr = FakeASR()
    vision = FakeVision()
    summarizer = Summarizer(llm, asr, vision, FakeConfig())

    result = run(summarizer.summarize(resource(), video_path=tmp_path / "clip.mp4"))
    assert result["pipeline"]["keyframes"] == "skipped-no-video"
    assert vision.frame_inputs == []
    assert result["visual_facts"] == []


def test_empty_transcription_marks_empty(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    llm = FakeLLM("{}")
    asr = FakeASR(transcription=Transcription(text="", language=None, segments=[]))
    summarizer = Summarizer(llm, asr, FakeVision(available=False), FakeConfig())

    result = run(summarizer.summarize(resource(), video_path=tmp_path / "clip.mp4"))
    assert result["pipeline"]["transcript"] == "empty"
    # No speech text, but keyframes were still extracted for the host agent.
    assert result["pipeline"]["source"] == "frames+caption"


def test_long_transcript_is_truncated(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    long_text = "啊" * 20000
    asr = FakeASR(transcription=Transcription(text=long_text, language="zh", segments=[]))
    llm = FakeLLM("{}")
    run(Summarizer(llm, asr, None, FakeConfig()).summarize(
        resource(), video_path=tmp_path / "clip.mp4"
    ))
    prompt = llm.calls[0]["messages"][0].content
    assert prompt.count("啊") == 12000


def test_format_ms_helper():
    assert _format_ms(None) == "??:??:??"
    assert _format_ms(0) == "00:00:00"
    assert _format_ms(1500) == "00:00:01"
    assert _format_ms(3_723_000) == "01:02:03"
    assert _format_ms(-5) == "00:00:00"


# --------------------------------------------------------------------------- #
# Key-free evidence collection (no server-side LLM)
# --------------------------------------------------------------------------- #
def test_collect_evidence_bundle_exposes_segments_and_frame_paths(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    summarizer = Summarizer(None, FakeASR(), None, FakeConfig())

    bundle = run(summarizer.collect_evidence(resource(), video_path=tmp_path / "clip.mp4"))
    payload = bundle.to_dict()

    assert payload["transcript"] == "完整 转写"
    assert payload["transcript_segments"] == [
        {"start_ms": 0, "end_ms": 1500, "text": "完整"},
        {"start_ms": 1500, "end_ms": 3000, "text": "转写"},
    ]
    assert len(payload["frame_paths"]) == 2
    assert payload["frames_extracted"] == 2
    assert payload["pipeline"]["transcript"] == "ok"
    assert payload["pipeline"]["keyframes"] == "extracted"
    assert payload["pipeline"]["source"] == "transcript+caption"


def test_collect_evidence_without_video_is_caption_only():
    summarizer = Summarizer(None, FakeASR(), FakeVision(), FakeConfig())
    bundle = run(summarizer.collect_evidence(resource()))  # no local file

    assert bundle.video_path is None
    assert bundle.frame_paths == []
    assert bundle.transcription is None
    status = bundle.to_dict()["pipeline"]
    assert status["transcript"] == "off"
    assert status["keyframes"] == "off"
    assert status["source"] == "caption-only"


def test_summarize_without_llm_raises_and_points_to_evidence_path():
    summarizer = Summarizer(None)
    with pytest.raises(LLMError, match="collect_evidence"):
        run(summarizer.summarize(resource()))


def test_extraction_error_marker_survives_without_vision(monkeypatch, tmp_path):
    patch_media(monkeypatch)

    async def boom(video, out_dir, *, max_frames, width):
        raise media.MediaToolError("ffmpeg exited 1")

    monkeypatch.setattr(media, "extract_keyframes", boom)
    # No VLM analyzer: _describe_frames must preserve the extraction error
    # rather than overwriting it with "extracted"/"not-configured".
    summarizer = Summarizer(FakeLLM("{}"), FakeASR(), None, FakeConfig())
    result = run(summarizer.summarize(resource(), video_path=tmp_path / "clip.mp4"))
    assert result["pipeline"]["keyframes"] == "error"
    assert any("keyframes: MediaToolError" in e for e in result["pipeline"]["errors"])


# --------------------------------------------------------------------------- #
# M3: speaker diarization and scene-aware frames through the pipeline
# --------------------------------------------------------------------------- #
def _gapped_transcription():
    return Transcription(
        text="你好 我来说",
        language="zh",
        segments=[
            TranscriptSegment(0, 500, "你好"),
            TranscriptSegment(2000, 2500, "我来说"),  # 1500ms gap -> new turn
        ],
    )


def test_diarization_attaches_speaker_labels_to_evidence(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    summarizer = Summarizer(
        FakeLLM("{}"), FakeASR(_gapped_transcription()), None, FakeConfig(),
        diarizer=PauseGapDiarizer(700),
    )
    bundle = run(summarizer.collect_evidence(resource(), video_path=tmp_path / "c.mp4"))
    payload = bundle.to_dict()
    assert [t["speaker"] for t in payload["speaker_turns"]] == [
        "SPEAKER_A", "SPEAKER_B",
    ]
    # speaker is folded into each transcript segment in the evidence payload
    assert payload["transcript_segments"][0]["speaker"] == "SPEAKER_A"
    assert payload["transcript_segments"][1]["speaker"] == "SPEAKER_B"
    assert payload["pipeline"]["diarization"] == "pause-gap-heuristic"


def test_diarization_off_by_default_has_no_speaker_key(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    summarizer = Summarizer(FakeLLM("{}"), FakeASR(), None, FakeConfig())
    bundle = run(summarizer.collect_evidence(resource(), video_path=tmp_path / "c.mp4"))
    payload = bundle.to_dict()
    assert payload["speaker_turns"] == []
    assert payload["pipeline"]["diarization"] == "off"
    assert all("speaker" not in seg for seg in payload["transcript_segments"])


def test_diarization_skipped_without_transcript(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    empty = Transcription(text="", language=None, segments=[])
    summarizer = Summarizer(
        FakeLLM("{}"), FakeASR(empty), None, FakeConfig(),
        diarizer=PauseGapDiarizer(700),
    )
    bundle = run(summarizer.collect_evidence(resource(), video_path=tmp_path / "c.mp4"))
    assert bundle.status.diarization == "skipped-no-transcript"
    assert bundle.speaker_turns == []


class _BoomDiarizer:
    name = "stub"

    def diarize(self, segments):
        raise DiarizationError("model exploded")


def test_diarization_error_is_recorded_not_raised(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    summarizer = Summarizer(
        FakeLLM("{}"), FakeASR(), None, FakeConfig(), diarizer=_BoomDiarizer(),
    )
    bundle = run(summarizer.collect_evidence(resource(), video_path=tmp_path / "c.mp4"))
    assert bundle.status.diarization == "error"
    assert bundle.speaker_turns == []
    assert any("diarization: DiarizationError" in e for e in bundle.status.errors)


def test_summarize_surface_includes_speaker_turns(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    summarizer = Summarizer(
        FakeLLM('{"tldr": "要点", "language": "zh"}'),
        FakeASR(_gapped_transcription()), None, FakeConfig(),
        diarizer=PauseGapDiarizer(700),
    )
    result = run(summarizer.summarize(resource(), video_path=tmp_path / "c.mp4"))
    assert [t["speaker"] for t in result["speaker_turns"]] == ["SPEAKER_A", "SPEAKER_B"]
    assert result["pipeline"]["diarization"] == "pause-gap-heuristic"


class _HybridConfig:
    keyframe_max = 6
    keyframe_width = 480
    keyframe_mode = "hybrid"
    scene_threshold = 0.5


def test_hybrid_keyframe_mode_uses_detailed_and_reports_cuts(monkeypatch, tmp_path):
    patch_media(monkeypatch)
    captured = {}

    async def fake_detailed(video, out_dir, *, max_frames, width, mode, scene_threshold):
    # capture kwargs then emulate outputs
        captured.update(mode=mode, threshold=scene_threshold, max_frames=max_frames)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        made = [out_dir / "frame_001.jpg", out_dir / "frame_002.jpg"]
        for p in made:
            p.write_bytes(b"jpeg")
        return media.KeyframeResult(made, 4)

    monkeypatch.setattr(media, "extract_keyframes_detailed", fake_detailed)
    summarizer = Summarizer(FakeLLM("{}"), FakeASR(), None, _HybridConfig())
    bundle = run(summarizer.collect_evidence(resource(), video_path=tmp_path / "c.mp4"))
    payload = bundle.to_dict()
    assert captured == {"mode": "hybrid", "threshold": 0.5, "max_frames": 6}
    assert payload["pipeline"]["keyframe_mode"] == "hybrid"
    assert payload["pipeline"]["scene_cuts"] == 4
    assert payload["scene_cuts"] == 4
    assert payload["frames_extracted"] == 2