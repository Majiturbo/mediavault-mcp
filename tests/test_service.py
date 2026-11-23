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

    def __init__(self):
        self.download_calls: list[tuple] = []

    async def download(self, resource, quality="compat"):
        self.download_calls.append((resource.canonical_id, quality))
        return [f"/tmp/vault/{resource.platform}/x.mp4"]


class FakeRegistry:
    def __init__(self, resource: Resource, fail: bool = False):
        self._resource = resource
        self._fail = fail
        self.adapter = FakeAdapter()
        self.probe_calls: list[str] = []

    async def probe(self, url):
        self.probe_calls.append(url.raw_url)
        if self._fail:
            raise AdapterError("all adapters failed: boom")
        return self._resource

    def chain_for(self, url):
        return [self.adapter]


class FakeSummarizer:
    def __init__(self):
        self.calls: list[Resource] = []

    async def summarize(self, resource: Resource) -> dict:
        self.calls.append(resource)
        return {"tldr": "fake summary", "pipeline": {"source": "caption-only"}}


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


def make_service(tmp_path, monkeypatch, resource=None, fail=False):
    config = Config.load({"MEDIAVAULT_HOME": str(tmp_path / "vault")})
    registry = FakeRegistry(resource or make_resource(), fail=fail)
    monkeypatch.setattr(service_module, "build_default_registry", lambda cfg: registry)
    svc = MediaVaultService(config=config)
    svc._summarizer = FakeSummarizer()
    return svc, registry


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

    async def wait_done():
        for _ in range(50):
            job = svc.jobs.get(job_id)
            if job.status in {"succeeded", "failed"}:
                return job
            await asyncio.sleep(0.02)
        return svc.jobs.get(job_id)

    job = run(wait_done())
    assert job.status == "succeeded"
    assert job.result["resource_id"] == "douyin:1"
    assert job.result["summary"]["tldr"] == "fake summary"
    assert isinstance(svc._summarizer.calls[0], Resource)


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
