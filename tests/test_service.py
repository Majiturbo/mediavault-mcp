"""Orchestration-layer tests with fake registry, adapter and summarizer."""

import asyncio

import pytest

from mediavault import service as service_module
from mediavault.adapters.base import AdapterError
from mediavault.config import Config
from mediavault.models import Author, Resource
from mediavault.service import MediaVaultService


class FakeAdapter:
    name = "fake-adapter"

    def __init__(self, files=None, fail: bool = False):
        self.download_calls: list[tuple] = []
        self._files = files or ["/tmp/vault/douyin/x.mp4"]
        self._fail = fail

    async def download(self, resource, quality="compat"):
        self.download_calls.append((resource.canonical_id, quality))
        if self._fail:
            raise AdapterError("download blocked: boom")
        return self._files


class FakeRegistry:
    def __init__(self, resource: Resource, fail: bool = False, adapter: FakeAdapter | None = None):
        self._resource = resource
        self._fail = fail
        self.adapter = adapter or FakeAdapter()
        self.probe_calls: list[str] = []

    async def probe(self, url):
        self.probe_calls.append(url.raw_url)
        if self._fail:
            raise AdapterError("all adapters failed: boom")
        return self._resource

    def chain_for(self, url):
        return [self.adapter]


class FakeBundle:
    def __init__(self, resource, video_path, *, transcript="off", keyframes="off"):
        self.resource = resource
        self.video_path = video_path

    def to_dict(self):
        return {
            "canonical_id": self.resource.canonical_id,
            "platform": self.resource.platform,
            "title": self.resource.title,
            "caption": self.resource.caption,
            "transcript": "",
            "transcript_segments": [],
            "frames_extracted": 0,
            "frame_paths": [],
            "visual_facts": [],
            "pipeline": {
                "source": "caption-only",
                "transcript": "off",
                "keyframes": "off",
                "frames_extracted": 0,
                "errors": [],
                "canonical_id": self.resource.canonical_id,
            },
        }


class FakeSummarizer:
    def __init__(self):
        self.calls: list[tuple] = []
        self.evidence_calls: list[tuple] = []

    async def summarize(self, resource: Resource, video_path=None) -> dict:
        self.calls.append((resource, video_path))
        return {
            "tldr": "fake summary",
            "pipeline": {
                "source": "caption-only",
                "transcript": "off",
                "keyframes": "off",
                "errors": [],
                "canonical_id": resource.canonical_id,
            },
        }

    async def collect_evidence(self, resource: Resource, video_path=None):
        self.evidence_calls.append((resource, video_path))
        return FakeBundle(resource, video_path)


def make_resource(canonical_id="douyin:1", platform="douyin"):
    return Resource(
        canonical_id=canonical_id,
        platform=platform,
        content_type="video",
        page_url="https://v.douyin.com/x/",
        title="测试",
        author=Author(name="博主"),
        caption="文案",
        adapter_used="fake-adapter",
    )


def make_service(tmp_path, monkeypatch, resource=None, fail=False, adapter=None):
    # Tests inject a FakeSummarizer, so a placeholder key is enough to pass
    # the summarize fast-fail gate (mirrors a local Ollama-style endpoint).
    config = Config.load({
        "MEDIAVAULT_HOME": str(tmp_path / "vault"),
        "OPENAI_BASE_URL": "http://localhost:11434/v1",
        "OPENAI_API_KEY": "test-placeholder",
    })
    registry = FakeRegistry(resource or make_resource(), fail=fail, adapter=adapter)
    monkeypatch.setattr(service_module, "build_default_registry", lambda cfg: registry)
    svc = MediaVaultService(config=config)
    svc._summarizer = FakeSummarizer()
    return svc, registry


async def _wait_job(svc, job_id):
    for _ in range(50):
        job = svc.jobs.get(job_id)
        if job.status in {"succeeded", "failed"}:
            return job
        await asyncio.sleep(0.02)
    return svc.jobs.get(job_id)


def run(coro):
    return asyncio.run(coro)


def test_parse_text_returns_normalized_dicts(tmp_path, monkeypatch):
    svc, registry = make_service(tmp_path, monkeypatch)
    results = run(svc.parse_text("看看 https://v.douyin.com/x/ 这个"))
    assert len(results) == 1
    assert results[0]["canonical_id"] == "douyin:1"
    assert results[0]["adapter_used"] == "fake-adapter"
    assert registry.probe_calls == ["https://v.douyin.com/x/"]


def test_parse_text_multiple_urls(tmp_path, monkeypatch):
    svc, registry = make_service(tmp_path, monkeypatch)
    results = run(svc.parse_text("a https://youtu.be/1 b https://x.com/u/status/2"))
    assert len(results) == 2
    assert len(registry.probe_calls) == 2


def test_parse_text_without_url_raises(tmp_path, monkeypatch):
    svc, _ = make_service(tmp_path, monkeypatch)
    with pytest.raises(AdapterError, match=r"no http\(s\) URL"):
        run(svc.parse_text("纯文字输入"))


def test_parse_text_propagates_adapter_failure(tmp_path, monkeypatch):
    svc, _ = make_service(tmp_path, monkeypatch, fail=True)
    with pytest.raises(AdapterError, match="boom"):
        run(svc.parse_text("https://v.douyin.com/x/"))


def test_download_validates_quality(tmp_path, monkeypatch):
    svc, _ = make_service(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="quality"):
        run(svc.download("https://v.douyin.com/x/", quality="ultra"))


def test_download_routes_to_producing_adapter(tmp_path, monkeypatch):
    svc, registry = make_service(tmp_path, monkeypatch)
    results = run(svc.download("https://v.douyin.com/x/", quality="speed"))
    assert len(results) == 1
    payload = results[0]
    assert payload["files"] == ["/tmp/vault/douyin/x.mp4"]
    assert payload["resource"]["canonical_id"] == "douyin:1"
    assert registry.adapter.download_calls == [("douyin:1", "speed")]


def test_download_without_url_raises(tmp_path, monkeypatch):
    svc, _ = make_service(tmp_path, monkeypatch)
    with pytest.raises(AdapterError, match=r"no http\(s\) URL"):
        run(svc.download("no link here"))


def test_summarize_submits_background_job(tmp_path, monkeypatch):
    svc, registry = make_service(tmp_path, monkeypatch)
    job_id = run(svc.summarize("https://v.douyin.com/x/"))
    assert isinstance(job_id, str) and len(job_id) >= 8

    job = run(_wait_job(svc, job_id))
    assert job.status == "succeeded"
    assert job.result["resource_id"] == "douyin:1"
    assert job.result["summary"]["tldr"] == "fake summary"
    resource_arg, video_path = svc._summarizer.calls[0]
    assert isinstance(resource_arg, Resource)
    # Default path downloads a speed-profile video for media analysis.
    assert video_path == "/tmp/vault/douyin/x.mp4"
    assert registry.adapter.download_calls == [("douyin:1", "speed")]


def test_summarize_with_media_false_skips_download(tmp_path, monkeypatch):
    svc, registry = make_service(tmp_path, monkeypatch)
    job_id = run(svc.summarize("https://v.douyin.com/x/", with_media=False))
    job = run(_wait_job(svc, job_id))
    assert job.status == "succeeded"
    assert registry.adapter.download_calls == []
    assert svc._summarizer.calls[0][1] is None


def test_summarize_download_failure_falls_back_to_caption(tmp_path, monkeypatch):
    adapter = FakeAdapter(fail=True)
    svc, _ = make_service(tmp_path, monkeypatch, adapter=adapter)
    job_id = run(svc.summarize("https://v.douyin.com/x/"))
    job = run(_wait_job(svc, job_id))
    assert job.status == "succeeded"
    pipeline = job.result["summary"]["pipeline"]
    assert pipeline["transcript"] == "skipped-no-local-file"
    assert pipeline["keyframes"] == "skipped-no-local-file"
    assert any("download:AdapterError" in e for e in pipeline["errors"])


def test_summarize_non_video_download_marks_note(tmp_path, monkeypatch):
    adapter = FakeAdapter(files=["/tmp/vault/douyin/pic.jpg"])
    svc, _ = make_service(tmp_path, monkeypatch, adapter=adapter)
    job_id = run(svc.summarize("https://v.douyin.com/x/"))
    job = run(_wait_job(svc, job_id))
    pipeline = job.result["summary"]["pipeline"]
    assert "download:no-video-file" in pipeline["errors"]
    assert svc._summarizer.calls[0][1] is None


def test_summarize_image_set_skips_media_pipeline(tmp_path, monkeypatch):
    image_resource = make_resource()
    image_resource.content_type = "image_set"
    svc, registry = make_service(tmp_path, monkeypatch, resource=image_resource)
    job_id = run(svc.summarize("https://www.xiaohongshu.com/note/1"))
    job = run(_wait_job(svc, job_id))
    assert job.status == "succeeded"
    assert registry.adapter.download_calls == []
    assert "skipped:image-set" in job.result["summary"]["pipeline"]["errors"]


def test_summarize_keeps_non_off_markers_when_degraded(tmp_path, monkeypatch):
    svc, _ = make_service(tmp_path, monkeypatch, adapter=FakeAdapter(fail=True))

    class ConfiguredButSkipped(FakeSummarizer):
        async def summarize(self, resource, video_path=None):
            payload = await super().summarize(resource, video_path=video_path)
            payload["pipeline"]["transcript"] = "not-configured"
            payload["pipeline"]["keyframes"] = "not-configured"
            return payload

    svc._summarizer = ConfiguredButSkipped()
    job_id = run(svc.summarize("https://v.douyin.com/x/"))
    job = run(_wait_job(svc, job_id))
    pipeline = job.result["summary"]["pipeline"]
    # Markers that explain why a stage did not run must not be overwritten.
    assert pipeline["transcript"] == "not-configured"
    assert pipeline["keyframes"] == "not-configured"
    assert any("download:AdapterError" in e for e in pipeline["errors"])


def test_pick_video_helper():
    pick = MediaVaultService._pick_video
    assert pick(["a.jpg", "b.MP4", "c.wav"]) == "b.MP4"
    assert pick(["a.jpg", "b.png"]) is None
    assert pick([]) is None


def _keyless_service(tmp_path, monkeypatch, resource=None, adapter=None):
    config = Config.load({"MEDIAVAULT_HOME": str(tmp_path / "vault")})  # no key
    registry = FakeRegistry(resource or make_resource(), adapter=adapter)
    monkeypatch.setattr(service_module, "build_default_registry", lambda cfg: registry)
    svc = MediaVaultService(config=config)
    svc._summarizer = FakeSummarizer()
    return svc, registry


def test_analyze_needs_no_key_and_collects_evidence(tmp_path, monkeypatch):
    svc, registry = _keyless_service(tmp_path, monkeypatch)
    payload = run(svc.analyze("https://v.douyin.com/x/"))

    assert payload["canonical_id"] == "douyin:1"
    assert payload["frame_paths"] == []
    assert payload["pipeline"]["errors"] == []
    # The speed-profile clip was downloaded for evidence collection.
    assert registry.adapter.download_calls == [("douyin:1", "speed")]
    evidence_video = svc._summarizer.evidence_calls[0][1]
    assert evidence_video == "/tmp/vault/douyin/x.mp4"


def test_analyze_reports_single_url_envelope(tmp_path, monkeypatch):
    svc, _ = _keyless_service(tmp_path, monkeypatch)
    payload = run(svc.analyze("https://v.douyin.com/x/"))
    assert payload["analyzed_url"] == "https://v.douyin.com/x/"
    assert payload["urls_detected"] == 1
    assert payload["additional_urls"] == []


def test_analyze_multi_url_processes_first_and_lists_rest(tmp_path, monkeypatch):
    svc, registry = _keyless_service(tmp_path, monkeypatch)
    text = "看看 https://v.douyin.com/a/ 和 https://x.com/b/status/1 还有 https://b23.tv/c"
    payload = run(svc.analyze(text))

    # Only the first link is probed/downloaded; the rest are surfaced, not dropped.
    assert registry.probe_calls == ["https://v.douyin.com/a/"]
    assert registry.adapter.download_calls == [("douyin:1", "speed")]
    assert payload["urls_detected"] == 3
    assert payload["additional_urls"] == [
        "https://x.com/b/status/1",
        "https://b23.tv/c",
    ]


def test_analyze_with_media_false_skips_download(tmp_path, monkeypatch):
    svc, registry = _keyless_service(tmp_path, monkeypatch)
    run(svc.analyze("https://v.douyin.com/x/", with_media=False))
    assert registry.adapter.download_calls == []
    assert svc._summarizer.evidence_calls[0][1] is None


def test_analyze_download_failure_marks_skipped_no_local_file(tmp_path, monkeypatch):
    adapter = FakeAdapter(fail=True)
    svc, _ = _keyless_service(tmp_path, monkeypatch, adapter=adapter)
    payload = run(svc.analyze("https://v.douyin.com/x/"))

    pipeline = payload["pipeline"]
    assert pipeline["transcript"] == "skipped-no-local-file"
    assert pipeline["keyframes"] == "skipped-no-local-file"
    assert any("download:AdapterError" in e for e in pipeline["errors"])


def test_analyze_image_set_skips_media(tmp_path, monkeypatch):
    resource = make_resource()
    resource.content_type = "image_set"
    svc, registry = _keyless_service(tmp_path, monkeypatch, resource=resource)
    payload = run(svc.analyze("https://v.douyin.com/x/"))
    assert registry.adapter.download_calls == []
    assert "skipped:image-set" in payload["pipeline"]["errors"]


def test_analyze_without_url_raises(tmp_path, monkeypatch):
    svc, _ = _keyless_service(tmp_path, monkeypatch)
    with pytest.raises(AdapterError, match="no http"):
        run(svc.analyze("没有链接的纯文本"))


def test_analyze_keeps_specific_markers_when_download_fails(tmp_path, monkeypatch):
    adapter = FakeAdapter(fail=True)
    svc, _ = _keyless_service(tmp_path, monkeypatch, adapter=adapter)

    # Evidence already carries specific markers; the download note must not
    # flatten them into generic skipped-no-local-file values.
    class SpecificBundle(FakeBundle):
        def to_dict(self):
            payload = super().to_dict()
            payload["pipeline"]["transcript"] = "not-configured"
            payload["pipeline"]["keyframes"] = "extracted"
            return payload

    async def specific_evidence(resource, video_path=None):
        return SpecificBundle(resource, video_path)

    svc._summarizer.collect_evidence = specific_evidence
    payload = run(svc.analyze("https://v.douyin.com/x/"))
    pipeline = payload["pipeline"]
    assert pipeline["transcript"] == "not-configured"
    assert pipeline["keyframes"] == "extracted"
    assert any("download:AdapterError" in e for e in pipeline["errors"])


def test_summarize_without_key_fails_fast_and_does_not_download(tmp_path, monkeypatch):
    config = Config.load({"MEDIAVAULT_HOME": str(tmp_path / "vault")})  # no OPENAI_API_KEY
    registry = FakeRegistry(make_resource())
    monkeypatch.setattr(service_module, "build_default_registry", lambda cfg: registry)
    svc = MediaVaultService(config=config)

    with pytest.raises(service_module.LLMError, match="OPENAI_API_KEY|OpenAI-compatible"):
        run(svc.summarize("https://v.douyin.com/x/"))
    # No job queued and no download attempted.
    assert svc.list_jobs() == []
    assert registry.adapter.download_calls == []


def test_summarize_placeholder_key_allows_local_endpoint(tmp_path, monkeypatch):
    # A non-empty placeholder key must pass the fast-fail gate (local servers
    # do not validate it); the job then proceeds normally.
    config = Config.load({
        "MEDIAVAULT_HOME": str(tmp_path / "vault"),
        "OPENAI_BASE_URL": "http://localhost:11434/v1",
        "OPENAI_API_KEY": "ollama",
    })
    registry = FakeRegistry(make_resource())
    monkeypatch.setattr(service_module, "build_default_registry", lambda cfg: registry)
    svc = MediaVaultService(config=config)
    svc._summarizer = FakeSummarizer()

    job_id = run(svc.summarize("https://v.douyin.com/x/"))
    job = run(_wait_job(svc, job_id))
    assert job.status == "succeeded"


def test_summarize_job_fails_on_missing_url(tmp_path, monkeypatch):
    svc, _ = make_service(tmp_path, monkeypatch)
    job_id = run(svc.summarize("没有链接"))

    async def wait_done():
        for _ in range(50):
            job = svc.jobs.get(job_id)
            if job.status in {"succeeded", "failed"}:
                return job
            await asyncio.sleep(0.02)
        return svc.jobs.get(job_id)

    job = run(wait_done())
    assert job.status == "failed"
    assert "AdapterError" in (job.error or "")


def test_job_helpers(tmp_path, monkeypatch):
    svc, _ = make_service(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        svc.get_job("nope")
    assert svc.list_jobs() == []


def test_service_close_stops_job_loop(tmp_path, monkeypatch):
    svc, _ = make_service(tmp_path, monkeypatch)
    # A submitted job forces the worker loop to start.
    run(svc.summarize("https://v.douyin.com/x/"))
    run(_wait_job(svc, svc.list_jobs()[0]["id"]))
    assert svc.jobs._loop is not None
    svc.close()
    assert svc.jobs._loop is None
    svc.close()  # idempotent after shutdown
