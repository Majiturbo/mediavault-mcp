"""Fallback-chain behavior with fake adapters; no network access."""

import asyncio

import pytest

from mediavault.adapters.base import Adapter, AdapterError, AdapterStale
from mediavault.adapters.registry import AdapterRegistry, build_default_registry
from mediavault.config import Config
from mediavault.models import Resource
from mediavault.routing import canonicalize


class FakeAdapter(Adapter):
    def __init__(self, name: str, score: int, behavior: str):
        super().__init__(
            name=name,
            config=Config.load({"MEDIAVAULT_HOME": "/tmp/mediavault-test"}),
            platforms=frozenset({"douyin"}),
        )
        self._score = score
        self.behavior = behavior

    def match_score(self, url):
        return self._score

    async def probe(self, url):
        if self.behavior == "stale":
            raise AdapterStale("page changed")
        if self.behavior == "error":
            raise AdapterError("blocked")
        return Resource(
            canonical_id="douyin:1",
            platform="douyin",
            content_type="video",
            page_url=url.raw_url,
            adapter_used=self.name,
        )

    async def download(self, resource, quality="compat"):
        return ["/tmp/fake.mp4"]


def _url():
    return canonicalize("https://v.douyin.com/x/")


async def _falls_through():
    registry = AdapterRegistry([
        FakeAdapter("first", 200, "stale"),
        FakeAdapter("second", 100, "ok"),
    ])
    resource = await registry.probe(_url())
    assert resource.adapter_used == "second"
    assert resource.degraded is True
    assert any("first" in note for note in resource.notes)
    assert resource.fallback_chain == ["first", "second"]


async def _all_fail():
    registry = AdapterRegistry([
        FakeAdapter("first", 200, "stale"),
        FakeAdapter("second", 100, "error"),
    ])
    with pytest.raises(AdapterError, match="all adapters failed"):
        await registry.probe(_url())


async def _higher_score_wins():
    registry = AdapterRegistry([
        FakeAdapter("low", 50, "ok"),
        FakeAdapter("high", 300, "ok"),
    ])
    resource = await registry.probe(_url())
    assert resource.adapter_used == "high"
    assert resource.degraded is False


def test_falls_through_stale_adapter():
    asyncio.run(_falls_through())


def test_all_fail_raises():
    asyncio.run(_all_fail())


def test_higher_score_wins():
    asyncio.run(_higher_score_wins())


def test_empty_registry_raises():
    registry = AdapterRegistry([])

    async def body():
        with pytest.raises(AdapterError, match="no adapter registered"):
            await registry.probe(canonicalize("https://v.douyin.com/x/"))

    asyncio.run(body())


def test_register_appends_adapter():
    registry = AdapterRegistry([])
    adapter = FakeAdapter("solo", 1, "ok")
    registry.register(adapter)
    assert registry.chain_for(canonicalize("https://v.douyin.com/x/")) == [adapter]


def test_default_registry_advertises_platforms():
    registry = build_default_registry(Config.load({"MEDIAVAULT_HOME": "/tmp/mediavault-test"}))
    adapter = registry.chain_for(canonicalize("https://www.youtube.com/watch?v=x"))[0]
    assert adapter.name == "yt-dlp"
    assert "youtube" in adapter.platforms
    assert "douyin" in adapter.platforms
    assert "unknown" in adapter.platforms
    assert "video" in adapter.capabilities
