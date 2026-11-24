"""Offline tests for the optional gallery-dl image-set adapter.

The gallery-dl binary is never invoked: _which and the _run subprocess seam
are monkeypatched. The argv/line-output contract is fixture-pinned so a live
install only has to satisfy the two argument builders.
"""

import json

import pytest

from mediavault.adapters.base import Adapter, AdapterError, AdapterStale
from mediavault.adapters.gallerydl_adapter import GalleryDLAdapter
from mediavault.adapters.registry import AdapterRegistry
from mediavault.config import Config
from mediavault.models import MediaItem, Resource
from mediavault.routing import canonicalize


def make_config(tmp_path, **extra):
    env = {"MEDIAVAULT_HOME": str(tmp_path / "vault"),
           "MEDIAVAULT_GALLERYDL_BIN": "gallery-dl", **extra}
    return Config.load(env)


def make_adapter(tmp_path, **extra):
    cfg = make_config(tmp_path, **extra)
    return GalleryDLAdapter(
        config=cfg, name="gallery-dl",
        platforms=GalleryDLAdapter.platforms,
        capabilities=GalleryDLAdapter.capabilities,
    )


def run(coro):
    import asyncio
    return asyncio.run(coro)


def enable(adapter, monkeypatch, path="/usr/local/bin/gallery-dl"):
    monkeypatch.setattr(adapter, "_which", lambda: path)


class FakeRunner:
    def __init__(self, stdout="", code=0, stderr="", write_file=True):
        self.stdout, self.code, self.stderr = stdout, code, stderr
        self.write_file = write_file
        self.calls: list[list[str]] = []

    async def __call__(self, args, out_dir):
        self.calls.append(args)
        out_dir.mkdir(parents=True, exist_ok=True)
        if self.code != 0:
            return self.code, "", self.stderr
        # download invocation (has -D) materializes a file
        if "-D" in args and self.write_file:
            (out_dir / "pic_001.jpg").write_bytes(b"fake-jpeg")
        return 0, self.stdout, ""


XHS_URLS = chr(10).join([
    "https://ci.xiaohongshu.com/a.jpg?sign=1",
    "https://ci.xiaohongshu.com/b.png",
    "https://ci.xiaohongshu.com/a.jpg?sign=1",   # exact duplicate dropped
    "not-a-url-line",
])


def test_match_score_and_support():
    cfg = Config.load({"MEDIAVAULT_HOME": "/tmp/x"})
    adapter = GalleryDLAdapter(config=cfg, name="gallery-dl",
                               platforms=GalleryDLAdapter.platforms)
    xhs = canonicalize("https://www.xiaohongshu.com/explore/abc")
    youtube = canonicalize("https://www.youtube.com/watch?v=1")
    assert adapter.match_score(xhs) == 150
    assert adapter.supports(xhs) is True
    assert adapter.match_score(youtube) == 10
    assert adapter.supports(youtube) is False


def test_partition_urls_filters_dedups_and_mixes():
    stdout = chr(10).join([
        "https://h/1.jpg", "https://h/2.webp", "https://h/3.MP4",
        "https://h/1.jpg", "junk", "https://h/4.mov?x=1",
        "https://h/data.json",  # http but neither image nor video extension
    ])
    images, videos = GalleryDLAdapter._partition_urls(stdout)
    assert images == ["https://h/1.jpg", "https://h/2.webp"]
    assert videos == ["https://h/3.MP4", "https://h/4.mov?x=1"]  # first-seen order


def test_missing_binary_raises_stale(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    monkeypatch.setattr(adapter, "_which", lambda: None)
    url = canonicalize("https://www.xiaohongshu.com/explore/abc")
    with pytest.raises(AdapterStale, match="not found on PATH"):
        run(adapter.probe(url))
    with pytest.raises(AdapterStale, match="not found on PATH"):
        run(adapter.download(Resource(
            canonical_id="xiaohongshu:abc", platform="xiaohongshu",
            content_type="image_set", page_url=url.raw_url)))


def test_probe_image_set_contract(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    runner = FakeRunner(stdout=XHS_URLS)
    monkeypatch.setattr(adapter, "_run", runner)

    url = canonicalize("https://www.xiaohongshu.com/explore/note-123")
    resource = run(adapter.probe(url))

    assert resource.canonical_id == "xiaohongshu:note-123"
    assert resource.platform == "xiaohongshu"
    assert resource.content_type == "image_set"
    assert resource.adapter_used == "gallery-dl"
    assert len(resource.items) == 2  # duplicate collapsed
    assert all(i.kind == "image" and i.no_watermark == "native" for i in resource.items)
    assert [i.index for i in resource.items] == [0, 1]
    # probe argv: binary, -g, url and no -D
    args = runner.calls[0]
    assert args[0] == "/usr/local/bin/gallery-dl"
    assert "-g" in args and "-D" not in args
    assert args[-1] == url.raw_url


def test_probe_mixed_content_type(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    monkeypatch.setattr(adapter, "_run", FakeRunner(
        stdout=chr(10).join(["https://h/a.jpg", "https://h/b.mp4"])))
    resource = run(adapter.probe(
        canonicalize("https://www.instagram.com/p/abc123/")))
    assert resource.content_type == "mixed"
    kinds = [i.kind for i in resource.items]
    assert kinds == ["image", "video"]
    assert resource.items[1].no_watermark == "unknown"


def test_probe_empty_output_is_stale(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    monkeypatch.setattr(adapter, "_run", FakeRunner(stdout="status lines only"))
    with pytest.raises(AdapterStale, match="no recognizable media"):
        run(adapter.probe(canonicalize("https://www.xiaohongshu.com/x/y")))


def test_nonzero_exit_stale_vs_hard_error(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    url = canonicalize("https://www.xiaohongshu.com/x/y")

    monkeypatch.setattr(adapter, "_run",
                        FakeRunner(code=1, stderr="Error: login required (cookie)"))
    with pytest.raises(AdapterStale):
        run(adapter.probe(url))

    monkeypatch.setattr(adapter, "_run",
                        FakeRunner(code=2, stderr="connection reset"))
    with pytest.raises(AdapterError, match="gallery-dl failed"):
        run(adapter.probe(url))


def test_proxy_and_cookies_in_args(tmp_path, monkeypatch):
    adapter = make_adapter(
        tmp_path,
        MEDIAVAULT_PROXY="http://127.0.0.1:7890",
        MEDIAVAULT_COOKIES_FILE="/tmp/cookies.txt",
    )
    enable(adapter, monkeypatch)
    runner = FakeRunner(stdout="https://h/a.jpg")
    monkeypatch.setattr(adapter, "_run", runner)
    run(adapter.probe(canonicalize("https://www.xiaohongshu.com/x/y")))
    args = runner.calls[0]
    assert "--proxy" in args and "http://127.0.0.1:7890" in args
    assert "--cookies" in args and "/tmp/cookies.txt" in args


def test_real_which_and_subprocess(tmp_path, monkeypatch):
    import sys
    adapter = make_adapter(tmp_path)
    monkeypatch.setattr(
        "mediavault.adapters.gallerydl_adapter.shutil.which",
        lambda name: "/bin/gallery-dl" if name == "gallery-dl" else None)
    assert adapter._which() == "/bin/gallery-dl"

    out_dir = tmp_path / "run"
    code, stdout, _ = run(adapter._run(
        [sys.executable, "-c", "print('https://h/x.jpg')"], out_dir))
    assert code == 0 and "https://h/x.jpg" in stdout
    assert out_dir.is_dir()


def test_download_writes_files_and_resource_json(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    runner = FakeRunner()
    monkeypatch.setattr(adapter, "_run", runner)

    resource = Resource(
        canonical_id="xiaohongshu:note-123", platform="xiaohongshu",
        content_type="image_set",
        page_url="https://www.xiaohongshu.com/explore/note-123",
        items=[MediaItem(kind="image", url="https://h/a.jpg")],
    )
    files = run(adapter.download(resource))
    assert len(files) == 1 and files[0].endswith(".jpg")
    out_dir = tmp_path / "vault" / "xiaohongshu" / "note-123"
    assert (out_dir / "resource.json").is_file()
    saved = json.loads((out_dir / "resource.json").read_text(encoding="utf-8"))
    assert saved["canonical_id"] == "xiaohongshu:note-123"
    assert resource.items[0].local_path == files[0]
    # download argv uses -D with the target directory
    args = runner.calls[0]
    assert "-D" in args and str(out_dir) in args


def test_download_nonzero_exit_raises(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    monkeypatch.setattr(adapter, "_run", FakeRunner(code=3, stderr="disk full"))
    resource = Resource(
        canonical_id="instagram:p1", platform="instagram",
        content_type="image_set", page_url="https://www.instagram.com/p/p1/")
    with pytest.raises(AdapterError, match="gallery-dl failed"):
        run(adapter.download(resource))


class StubNext(Adapter):
    platforms = frozenset({"xiaohongshu"})
    capabilities = frozenset({"image_set"})

    def __init__(self, config):
        super().__init__(config=config, name="yt-dlp",
                         platforms=self.platforms, capabilities=self.capabilities)
        self.probed = False

    async def probe(self, url):
        self.probed = True
        return Resource(
            canonical_id="xiaohongshu:fallback", platform="xiaohongshu",
            content_type="image_set", page_url=url.raw_url,
            adapter_used="yt-dlp")

    async def download(self, resource, quality="compat"):
        return []


def test_registry_falls_through_when_gallerydl_stale(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    gallery = make_adapter(tmp_path)
    monkeypatch.setattr(gallery, "_which", lambda: None)  # absent -> Stale
    nxt = StubNext(config)
    registry = AdapterRegistry([gallery, nxt])

    url = canonicalize("https://www.xiaohongshu.com/explore/abc")
    assert [a.name for a in registry.chain_for(url)] == ["gallery-dl", "yt-dlp"]
    resource = run(registry.probe(url))
    assert nxt.probed is True
    assert resource.adapter_used == "yt-dlp"
    assert resource.degraded is True
    assert resource.fallback_chain == ["gallery-dl", "yt-dlp"]


def test_default_registry_orders_three_adapters(tmp_path):
    from mediavault.adapters.registry import build_default_registry
    reg = build_default_registry(make_config(tmp_path))
    url = canonicalize("https://www.xiaohongshu.com/explore/abc")
    assert [a.name for a in reg.chain_for(url)] == ["f2", "gallery-dl", "yt-dlp"]
