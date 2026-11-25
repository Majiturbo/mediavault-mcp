"""Offline tests for the M4 browser adapter (in-page fetch + CDN download)."""

import asyncio

import httpx
import pytest

from mediavault.adapters.base import AdapterError, AdapterStale
from mediavault.adapters.browser_adapter import BrowserYtDlpAdapter
from mediavault.browser_session import BrowserChallengeError, BrowserUnavailable
from mediavault.config import Config
from mediavault.models import MediaItem, Resource
from mediavault.routing import canonicalize


def make_config(tmp_path, enabled=True):
    return Config.load({
        "MEDIAVAULT_HOME": str(tmp_path / "vault"),
        "MEDIAVAULT_BROWSER_BOOTSTRAP": "on" if enabled else "off",
    })


def make_adapter(tmp_path, enabled=True):
    cfg = make_config(tmp_path, enabled)
    return BrowserYtDlpAdapter(
        config=cfg, name="browser",
        platforms=BrowserYtDlpAdapter.platforms,
        capabilities=BrowserYtDlpAdapter.capabilities,
    )


AWEME = {
    "aweme_id": "7693902023082825664",
    "desc": "入门 AI Agent 教程",
    "author": {"sec_uid": "sec123", "nickname": "在希Zaixi"},
    "video": {
        "duration": 15000,
        "play_addr": {
            "url_list": ["https://cdn.douyinvod.com/x.mp4?sign=abc"],
            "width": 1080, "height": 1920, "data_size": 9232470,
        },
    },
}


class FakeBootstrapper:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []
        self.expressions = []

    async def evaluate_after_ready(self, platform, url, expression, arg=None):
        self.calls.append((platform, url))
        self.expressions.append(expression)
        if self.error is not None:
            raise self.error
        return self.result


def run(coro):
    return asyncio.run(coro)


def test_score_and_support(tmp_path):
    adapter = make_adapter(tmp_path)
    douyin = canonicalize("https://v.douyin.com/x/")
    xhs = canonicalize("https://www.xiaohongshu.com/explore/y")
    youtube = canonicalize("https://www.youtube.com/watch?v=1")
    assert adapter.match_score(douyin) == 220 and adapter.supports(douyin)
    assert adapter.match_score(xhs) == 220 and adapter.supports(xhs)
    assert not adapter.supports(youtube)
    assert adapter.match_score(youtube) == 10


def test_probe_parses_in_page_aweme_detail(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    bs = FakeBootstrapper(result=AWEME)
    monkeypatch.setattr("mediavault.adapters.browser_adapter.build_bootstrapper", lambda c: bs)
    url = canonicalize("https://v.douyin.com/x/")
    resource = run(adapter.probe(url))
    assert bs.calls == [("douyin", url.raw_url)]
    from mediavault.platform_aweme import AWEME_DETAIL_JS
    assert bs.expressions == [AWEME_DETAIL_JS]
    assert resource.adapter_used == "browser"
    assert resource.canonical_id == "douyin:7693902023082825664"
    assert resource.content_type == "video" and resource.title == "入门 AI Agent 教程"
    assert resource.author.name == "在希Zaixi" and resource.author.id == "sec123"
    item = resource.items[0]
    assert item.kind == "video" and item.url.endswith("x.mp4?sign=abc")
    assert (item.width, item.height, item.duration_ms, item.size_hint, item.ext) == (
        1080, 1920, 15000, 9232470, "mp4")
    assert item.no_watermark == "unknown"
    assert any("no account login" in n for n in resource.notes)


@pytest.mark.parametrize("detail", [None, {}, {"aweme_id": None}])
def test_probe_blocked_or_shape_changed_is_stale(tmp_path, monkeypatch, detail):
    adapter = make_adapter(tmp_path)
    monkeypatch.setattr("mediavault.adapters.browser_adapter.build_bootstrapper",
                        lambda c: FakeBootstrapper(result=detail))
    with pytest.raises(AdapterStale):
        run(adapter.probe(canonicalize("https://v.douyin.com/x/")))


@pytest.mark.parametrize("error, marker", [
    (BrowserUnavailable("no playwright"), "browser unavailable"),
    (BrowserChallengeError("still walled"), "browser challenge not cleared"),
])
def test_probe_translates_bootstrap_errors(tmp_path, monkeypatch, error, marker):
    adapter = make_adapter(tmp_path)
    monkeypatch.setattr("mediavault.adapters.browser_adapter.build_bootstrapper",
                        lambda c: FakeBootstrapper(error=error))
    with pytest.raises(AdapterStale, match=marker):
        run(adapter.probe(canonicalize("https://v.douyin.com/x/")))


def test_probe_disabled_switch_is_stale(tmp_path):
    adapter = make_adapter(tmp_path, enabled=False)
    with pytest.raises(AdapterStale, match="disabled"):
        run(adapter.probe(canonicalize("https://v.douyin.com/x/")))


def test_download_writes_files_and_sets_local_paths(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    written = []
    async def fake_stream(client, item, target):
        target.write_bytes(b"media")
        written.append((item.kind, target.name))
    monkeypatch.setattr(adapter, "_stream", fake_stream)
    resource = Resource(
        canonical_id="douyin:7693", platform="douyin", content_type="video",
        page_url="https://v.douyin.com/x/", adapter_used="browser",
        items=[
            MediaItem(kind="video", url="https://cdn/a.mp4", ext="mp4"),
            MediaItem(kind="image", url="https://cdn/b.jpg"),
            MediaItem(kind="image", url=None),  # skipped, no URL
        ],
    )
    files = run(adapter.download(resource, "compat"))
    assert len(files) == 2 and all(f.startswith(str(tmp_path)) for f in files)
    assert written == [("video", "7693_0.mp4"), ("image", "7693_1.jpg")]
    assert all(item.local_path for item in resource.items[:2])


def test_download_stream_http_error_is_hard_failure(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    async def boom(client, item, target):
        raise httpx.ConnectError("reset")
    monkeypatch.setattr(adapter, "_stream", boom)
    resource = Resource(
        canonical_id="douyin:1", platform="douyin", content_type="video",
        page_url="u", adapter_used="browser",
        items=[MediaItem(kind="video", url="https://cdn/a.mp4")],
    )
    with pytest.raises(AdapterError, match="CDN download failed"):
        run(adapter.download(resource))


def test_download_without_urls_raises(tmp_path):
    adapter = make_adapter(tmp_path)
    resource = Resource(
        canonical_id="douyin:1", platform="douyin", content_type="video",
        page_url="u", adapter_used="browser", items=[],
    )
    with pytest.raises(AdapterError, match="no downloadable media"):
        run(adapter.download(resource))


class FakeResponse:
    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for chunk in (b"abc", b"def"):
            yield chunk


class FakeStream:
    async def __aenter__(self):
        return FakeResponse()

    async def __aexit__(self, *exc):
        return False


class FakeClient:
    def stream(self, method, url):
        self.method, self.url = method, url
        return FakeStream()


def test_stream_writes_chunked_bytes(tmp_path):
    client = FakeClient()
    target = tmp_path / "clip.mp4"
    item = MediaItem(kind="video", url="https://cdn/a.mp4")
    run(BrowserYtDlpAdapter._stream(client, item, target))
    assert target.read_bytes() == b"abcdef"
    assert client.method == "GET" and client.url == item.url

XHS_NOTE = {
    "noteId": "6affb9ba",
    "title": "广州胶片写真",
    "desc": "互免约拍",
    "user": {"userId": "u1", "nickname": "摄影师"},
    "imageList": [
        {"urlDefault": "https://ci/a.jpg", "width": 1080, "height": 1440},
        {"urlDefault": "https://ci/b.jpg"},
    ],
}


def test_probe_dispatches_xhs_note_parser(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    bs = FakeBootstrapper(result=XHS_NOTE)
    monkeypatch.setattr("mediavault.adapters.browser_adapter.build_bootstrapper", lambda c: bs)
    url = canonicalize("https://www.xiaohongshu.com/explore/y?xsec_token=t")
    resource = run(adapter.probe(url))
    from mediavault.platform_xhs import XHS_NOTE_JS
    assert bs.calls == [("xiaohongshu", url.raw_url)]
    assert bs.expressions == [XHS_NOTE_JS]
    assert resource.canonical_id == "xhs:6affb9ba"
    assert resource.content_type == "image_set" and len(resource.items) == 2
    assert resource.author.name == "摄影师" and resource.adapter_used == "browser"


def test_probe_xhs_risk_control_returns_null_is_stale(tmp_path, monkeypatch):
    # risk/login page has no __INITIAL_STATE__ -> in-page script returns None
    adapter = make_adapter(tmp_path)
    monkeypatch.setattr("mediavault.adapters.browser_adapter.build_bootstrapper",
                        lambda c: FakeBootstrapper(result=None))
    url = canonicalize("https://www.xiaohongshu.com/explore/y?xsec_token=t")
    with pytest.raises(AdapterStale, match="note missing"):
        run(adapter.probe(url))

def test_unmapped_platform_degrades_to_stale(tmp_path):
    # kuaishou is not mapped to an in-page extractor yet; both the probe guard
    # and the direct fetch guard must raise Stale instead of crashing
    adapter = make_adapter(tmp_path)
    url = canonicalize("https://www.kuaishou.com/short-video/x")
    with pytest.raises(AdapterStale, match="not mapped"):
        run(adapter.probe(url))
    with pytest.raises(AdapterStale, match="not mapped"):
        run(adapter._fetch_detail(url))