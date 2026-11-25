"""Offline tests for the binary-free SSR adapter (httpx fully mocked)."""

import asyncio
import json

import httpx
import pytest

from mediavault.adapters.base import AdapterError, AdapterStale
from mediavault.adapters.ssr_adapter import SsrAdapter
from mediavault.config import Config
from mediavault.models import MediaItem, Resource
from mediavault.platform_xhs import parse_xhs_note
from mediavault.routing import canonicalize


def make_adapter(tmp_path):
    cfg = Config.load({"MEDIAVAULT_HOME": str(tmp_path / "vault")})
    return SsrAdapter(
        config=cfg, name="ssr",
        platforms=SsrAdapter.platforms, capabilities=SsrAdapter.capabilities,
    )


def run(coro):
    return asyncio.run(coro)


class FakeResponse:
    def __init__(self, text="", fail=False):
        self.text = text
        self._fail = fail

    def raise_for_status(self):
        if self._fail:
            raise httpx.HTTPError("http 500")


class FakeAsyncClient:
    def __init__(self, response=None, error=None, **kwargs):
        self.response, self.error, self.kwargs = response, error, kwargs
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        self.requests.append(url)
        if self.error is not None:
            raise self.error
        return self.response


def install_client(monkeypatch, response=None, error=None):
    holder = {"client": None}

    def factory(**kwargs):
        holder["client"] = FakeAsyncClient(response, error, **kwargs)
        return holder["client"]

    monkeypatch.setattr("mediavault.adapters.ssr_adapter.httpx.AsyncClient", factory)
    return holder


XHS_HTML = "window.__INITIAL_STATE__=" + json.dumps({
    "note": {"noteDetailMap": {"k": {"note": {
        "noteId": "6af1", "title": "胶片",
        "imageList": [
            {"urlDefault": "https://ci/a.jpg"},
            {"urlDefault": "https://ci/b.jpg"},
        ],
    }}}},
})

KS_HTML = "window.__APOLLO_STATE__=" + json.dumps({
    "defaultClient": {"VisionVideoDetailPhoto:3x": {
        "photoId": "3x", "caption": "hi", "photoUrl": "https://cdn/p.mp4",
    }},
})


def test_score_and_support(tmp_path):
    adapter = make_adapter(tmp_path)
    xhs = canonicalize("https://www.xiaohongshu.com/explore/y")
    ks = canonicalize("https://www.kuaishou.com/short-video/z")
    douyin = canonicalize("https://v.douyin.com/x/")
    assert adapter.match_score(xhs) == 175 and adapter.supports(xhs)
    assert adapter.match_score(ks) == 175 and adapter.supports(ks)
    assert not adapter.supports(douyin) and adapter.match_score(douyin) == 10


def test_probe_xhs_hydrated(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    holder = install_client(monkeypatch, FakeResponse(XHS_HTML))
    url = canonicalize("https://www.xiaohongshu.com/explore/y?xsec_token=t")
    resource = run(adapter.probe(url))
    assert holder["client"].requests == [url.raw_url]
    assert resource.canonical_id == "xhs:6af1" and len(resource.items) == 2
    assert resource.adapter_used == "ssr"
    assert any("server-rendered state" in n for n in resource.notes)


def test_probe_kuaishou_hydrated(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    install_client(monkeypatch, FakeResponse(KS_HTML))
    url = canonicalize("https://www.kuaishou.com/short-video/z")
    resource = run(adapter.probe(url))
    assert resource.canonical_id == "kuaishou:3x"
    assert resource.items[0].url == "https://cdn/p.mp4"


def test_probe_shell_page_is_stale(tmp_path, monkeypatch):
    # risk/login shell: 200 HTML but no hydrated detail -> parser Stale
    adapter = make_adapter(tmp_path)
    install_client(monkeypatch, FakeResponse("<html>login shell</html>"))
    url = canonicalize("https://www.xiaohongshu.com/explore/y")
    with pytest.raises(AdapterStale, match="note missing"):
        run(adapter.probe(url))


def test_probe_transport_error_is_stale(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    install_client(monkeypatch, error=httpx.ConnectError("refused"))
    url = canonicalize("https://www.xiaohongshu.com/explore/y")
    with pytest.raises(AdapterStale, match="ssr fetch failed"):
        run(adapter.probe(url))


def test_probe_http_status_error_is_stale(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    install_client(monkeypatch, FakeResponse(fail=True))
    url = canonicalize("https://www.kuaishou.com/short-video/z")
    with pytest.raises(AdapterStale, match="ssr fetch failed"):
        run(adapter.probe(url))


def test_probe_unmapped_platform_is_stale(tmp_path):
    adapter = make_adapter(tmp_path)
    url = canonicalize("https://v.douyin.com/x/")
    with pytest.raises(AdapterStale, match="not mapped"):
        run(adapter.probe(url))


def test_download_writes_files(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    written = []

    async def fake_stream(client, item, target):
        with open(target, "wb") as handle:
            handle.write(b"img")
        written.append((item.kind, str(target)))

    monkeypatch.setattr(
        "mediavault.adapters.ssr_adapter.BrowserYtDlpAdapter._stream", fake_stream)
    note = {"noteId": "n1", "imageList": [
        {"urlDefault": "https://ci/a.jpg"}, {"urlDefault": "https://ci/b.jpg"}]}
    resource = parse_xhs_note(note, "https://www.xiaohongshu.com/explore/y", "ssr")
    files = run(adapter.download(resource))
    assert len(files) == 2 and all(f.endswith(".jpg") for f in files)
    assert all(item.local_path for item in resource.items)
    assert sorted(kind for kind, _ in written) == ["image", "image"]


def test_download_stream_error_is_hard_failure(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)

    async def boom(client, item, target):
        raise httpx.ConnectError("cdn down")

    monkeypatch.setattr(
        "mediavault.adapters.ssr_adapter.BrowserYtDlpAdapter._stream", boom)
    resource = parse_xhs_note(
        {"noteId": "n2", "imageList": [{"urlDefault": "https://ci/a.jpg"}]},
        "https://www.xiaohongshu.com/explore/y", "ssr")
    with pytest.raises(AdapterError, match="ssr CDN download failed"):
        run(adapter.download(resource))


def test_download_without_urls_raises(tmp_path):
    adapter = make_adapter(tmp_path)
    resource = Resource(
        canonical_id="xhs:n3", platform="xiaohongshu", content_type="image_set",
        page_url="https://www.xiaohongshu.com/explore/y",
        items=[MediaItem(kind="image", url="")], adapter_used="ssr")
    with pytest.raises(AdapterError, match="no downloadable media URLs"):
        run(adapter.download(resource))