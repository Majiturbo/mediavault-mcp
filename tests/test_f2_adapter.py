"""Offline tests for the optional f2 CLI adapter and its registry fallback.

The f2 binary is never invoked: _which and the subprocess seam _run are
monkeypatched. Fixtures pin the metadata JSON contract the defensive parser
normalizes; a live f2 install only has to satisfy the argv builders.
"""

import json

import pytest

from mediavault.adapters.base import Adapter, AdapterError, AdapterStale
from mediavault.adapters.f2_adapter import F2Adapter
from mediavault.adapters.registry import AdapterRegistry
from mediavault.config import Config
from mediavault.models import MediaItem, Resource
from mediavault.routing import canonicalize


def make_config(tmp_path):
    return Config.load({
        "MEDIAVAULT_HOME": str(tmp_path / "vault"),
        "MEDIAVAULT_F2_BIN": "f2",
    })


def make_adapter(tmp_path):
    cfg = make_config(tmp_path)
    return F2Adapter(
        config=cfg, name="f2",
        platforms=F2Adapter.platforms, capabilities=F2Adapter.capabilities,
    )


def enable(adapter, monkeypatch, path="/usr/local/bin/f2"):
    monkeypatch.setattr(adapter, "_which", lambda: path)


class FakeRunner:
    """Stand-in for F2Adapter._run: writes fixture metadata / a media file."""

    def __init__(self, payload=None, code=0, stderr="", write_meta=True):
        self.payload = payload
        self.code = code
        self.stderr = stderr
        self.write_meta = write_meta
        self.calls: list[list[str]] = []

    async def __call__(self, args, out_dir):
        self.calls.append(args)
        out_dir.mkdir(parents=True, exist_ok=True)
        if self.code != 0:
            return self.code, "", self.stderr
        if self.write_meta and self.payload is not None:
            (out_dir / "metadata.json").write_text(
                json.dumps(self.payload), encoding="utf-8"
            )
        (out_dir / "clip.mp4").write_bytes(b"fake-mp4")
        return 0, "ok", ""


DOUYIN_FIXTURE = {
    "aweme_id": "7401",
    "desc": "抖音测试视频",
    "author": {"nickname": "抖音博主"},
    "video": {
        "play_addr": {
            "uri": "v0d00465",
            "url_list": [
                "https://example-cdn.com/play.mp4?sign=abc",
                "https://example-cdn.com/backup.mp4",
            ],
        }
    },
}

XHS_FIXTURE = {
    "note_id": "65ab",
    "note_title": "小红书图文",
    "user": {"nickname": "小红薯"},
    "image_list": [
        {"url": "https://xhs.example/a.jpg?imageView=1"},
        {"url": "https://xhs.example/b.png"},
        {"url": "https://xhs.example/c.webp"},
    ],
}


def run(coro):
    import asyncio
    return asyncio.run(coro)


def test_match_score_outranks_generic_adapter():
    cfg = Config.load({"MEDIAVAULT_HOME": "/tmp/x"})
    f2 = F2Adapter(config=cfg, name="f2", platforms=F2Adapter.platforms)
    douyin = canonicalize("https://v.douyin.com/abc/")
    youtube = canonicalize("https://www.youtube.com/watch?v=1")
    assert f2.match_score(douyin) == 200
    assert f2.supports(douyin) is True
    assert f2.match_score(youtube) == 10
    assert f2.supports(youtube) is False


def test_missing_binary_raises_stale(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    monkeypatch.setattr(adapter, "_which", lambda: None)
    url = canonicalize("https://v.douyin.com/abc/")
    with pytest.raises(AdapterStale, match="not found on PATH"):
        run(adapter.probe(url))
    with pytest.raises(AdapterStale, match="not found on PATH"):
        run(adapter.download(Resource(
            canonical_id="douyin:1", platform="douyin",
            content_type="video", page_url=url.raw_url,
        )))


def test_probe_maps_nested_douyin_video(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    runner = FakeRunner(payload=DOUYIN_FIXTURE)
    monkeypatch.setattr(adapter, "_run", runner)

    url = canonicalize("https://v.douyin.com/abc/")
    resource = run(adapter.probe(url))

    assert resource.canonical_id == "douyin:7401"
    assert resource.platform == "douyin"
    assert resource.content_type == "video"
    assert resource.title == "抖音测试视频"
    assert resource.author.name == "抖音博主"
    assert resource.adapter_used == "f2"
    assert len(resource.items) == 1
    item = resource.items[0]
    assert item.kind == "video"
    assert item.url.endswith("play.mp4?sign=abc")
    assert item.no_watermark == "unknown"  # never claim removal we cannot prove
    # argv contract pinned in one place
    probe_args = runner.calls[0]
    assert probe_args[0] == "/usr/local/bin/f2"
    assert probe_args[1] == "dy"
    assert probe_args[-2:] == ["-M", "one"]
    assert "-u" in probe_args


def test_probe_maps_xhs_image_set_and_list_payload(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    runner = FakeRunner(payload=[XHS_FIXTURE])  # one-element batch envelope
    monkeypatch.setattr(adapter, "_run", runner)

    url = canonicalize("https://www.xiaohongshu.com/explore/65ab")
    resource = run(adapter.probe(url))

    assert resource.canonical_id == "xiaohongshu:65ab"
    assert resource.content_type == "image_set"
    assert resource.title == "小红书图文"
    assert [i.kind for i in resource.items] == ["image", "image", "image"]
    assert all(i.no_watermark == "native" for i in resource.items)
    assert runner.calls[0][1] == "xhs"


def test_probe_without_metadata_file_is_stale(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    monkeypatch.setattr(adapter, "_run", FakeRunner(payload=None))
    url = canonicalize("https://v.kuaishou.com/x/")
    with pytest.raises(AdapterStale, match="no metadata JSON"):
        run(adapter.probe(url))


def test_probe_malformed_json_is_stale(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)

    async def bad_run(args, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "metadata.json").write_text("{not json", encoding="utf-8")
        return 0, "", ""

    monkeypatch.setattr(adapter, "_run", bad_run)
    with pytest.raises(AdapterStale, match="not valid JSON"):
        run(adapter.probe(canonicalize("https://v.douyin.com/x/")))


def test_probe_unrecognizable_payload_is_stale(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    monkeypatch.setattr(adapter, "_run", FakeRunner(payload={"unexpected": 123}))
    with pytest.raises(AdapterStale, match="no recognizable media"):
        run(adapter.probe(canonicalize("https://v.douyin.com/x/")))


def test_nonzero_exit_stale_vs_hard_error(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    url = canonicalize("https://v.douyin.com/x/")

    monkeypatch.setattr(
        adapter, "_run", FakeRunner(code=1, stderr="Error: cookie expired, sign fail")
    )
    with pytest.raises(AdapterStale):
        run(adapter.probe(url))

    monkeypatch.setattr(
        adapter, "_run", FakeRunner(code=2, stderr="network unreachable")
    )
    with pytest.raises(AdapterError, match="f2 failed"):
        run(adapter.probe(url))


def test_download_materializes_files_and_resource_json(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    runner = FakeRunner()
    monkeypatch.setattr(adapter, "_run", runner)

    resource = Resource(
        canonical_id="douyin:7401", platform="douyin", content_type="video",
        page_url="https://v.douyin.com/abc/",
        items=[MediaItem(kind="video", url="https://example-cdn.com/play.mp4")],
    )
    files = run(adapter.download(resource, quality="compat"))

    assert files == [p for p in files if p.endswith(".mp4")]
    assert len(files) == 1
    out_dir = tmp_path / "vault" / "douyin" / "7401"
    assert (out_dir / "resource.json").is_file()
    saved = json.loads((out_dir / "resource.json").read_text(encoding="utf-8"))
    assert saved["canonical_id"] == "douyin:7401"
    assert resource.items[0].local_path == files[0]
    # No yt-dlp quality format string leaks into f2 argv.
    assert all("compat" not in arg for arg in runner.calls[0])


class StubYtDlp(Adapter):
    platforms = frozenset({"douyin", "unknown"})
    capabilities = frozenset({"video"})

    def __init__(self, config):
        super().__init__(config=config, name="yt-dlp",
                         platforms=self.platforms, capabilities=self.capabilities)
        self.probed = False

    async def probe(self, url):
        self.probed = True
        return Resource(
            canonical_id="douyin:fallback", platform="douyin",
            content_type="video", page_url=url.raw_url,
            title="fallback", adapter_used="yt-dlp",
        )

    async def download(self, resource, quality="compat"):
        return []


def test_registry_falls_back_to_ytdlp_when_f2_stale(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    f2 = F2Adapter(config=config, name="f2", platforms=F2Adapter.platforms)
    monkeypatch.setattr(f2, "_which", lambda: None)  # f2 unavailable -> Stale
    ytdlp = StubYtDlp(config)
    registry = AdapterRegistry([f2, ytdlp])

    url = canonicalize("https://v.douyin.com/abc/")
    assert [a.name for a in registry.chain_for(url)] == ["f2", "yt-dlp"]

    resource = run(registry.probe(url))
    assert ytdlp.probed is True
    assert resource.adapter_used == "yt-dlp"
    assert resource.degraded is True
    assert any("f2" in note and "stale" in note for note in resource.notes)
    assert resource.fallback_chain == ["f2", "yt-dlp"]


def test_proxy_argv_and_cookie_warning(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    adapter.config = Config.load({
        "MEDIAVAULT_HOME": str(tmp_path / "vault"),
        "MEDIAVAULT_PROXY": "socks5://127.0.0.1:1080",
        "MEDIAVAULT_COOKIES_FILE": str(tmp_path / "c.txt"),
    })
    enable(adapter, monkeypatch)
    runner = FakeRunner(payload=DOUYIN_FIXTURE)
    monkeypatch.setattr(adapter, "_run", runner)

    resource = run(adapter.probe(canonicalize("https://v.douyin.com/abc/")))
    args = runner.calls[0]
    assert "-P" in args and "socks5://127.0.0.1:1080" in args
    # f2 takes cookies via its own YAML/config options: warn instead of a bad file arg
    assert "--cookie" not in args and "-M" in args and "one" in args
    assert any("YAML config" in note for note in resource.notes)


def test_invalid_proxy_becomes_adapter_error(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    adapter.config = Config.load({
        "MEDIAVAULT_HOME": str(tmp_path / "vault"),
        "MEDIAVAULT_PROXY": "ftp://nope:1",
    })
    enable(adapter, monkeypatch)
    monkeypatch.setattr(adapter, "_run", FakeRunner(payload=DOUYIN_FIXTURE))
    with pytest.raises(AdapterError, match="not supported"):
        run(adapter.probe(canonicalize("https://v.douyin.com/abc/")))


def test_real_which_and_subprocess_runner(tmp_path, monkeypatch):
    import sys
    adapter = make_adapter(tmp_path)
    # Exercise the real shutil seam (f2 is expected absent in CI).
    monkeypatch.setattr("mediavault.adapters.f2_adapter.shutil.which",
                        lambda name: "/bin/f2" if name == "f2" else None)
    assert adapter._which() == "/bin/f2"

    # Drive the real asyncio subprocess wrapper with a harmless interpreter.
    out_dir = tmp_path / "run"
    code, stdout, stderr = run(adapter._run(
        [sys.executable, "-c", "print('hello-f2')"], out_dir
    ))
    assert code == 0
    assert "hello-f2" in stdout
    assert out_dir.is_dir()


def test_probe_unsupported_platform_is_hard_error(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    unknown = canonicalize("https://example.com/nonexistent/")
    with pytest.raises(AdapterError, match="no app for platform"):
        run(adapter.probe(unknown))


def test_download_unsupported_platform_is_hard_error(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    resource = Resource(
        canonical_id="example:1", platform="example",
        content_type="video", page_url="https://example.com/x",
    )
    with pytest.raises(AdapterError, match="no app for platform"):
        run(adapter.download(resource))


def test_download_nonzero_exit_raises(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    monkeypatch.setattr(adapter, "_run", FakeRunner(code=5, stderr="disk full"))
    resource = Resource(
        canonical_id="douyin:1", platform="douyin",
        content_type="video", page_url="https://v.douyin.com/x/",
        items=[MediaItem(kind="video", url="https://x/y.mp4")],
    )
    with pytest.raises(AdapterError, match="f2 failed"):
        run(adapter.download(resource))


def test_deep_find_traverses_nested_lists(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    nested = {"items": [{"meta": {}}, {"title": "嵌套标题"}],
              "aweme_id": "9", "video": {"play_url": "https://x/v.mp4"}}
    monkeypatch.setattr(adapter, "_run", FakeRunner(payload=nested))
    resource = run(adapter.probe(canonicalize("https://v.douyin.com/x/")))
    assert resource.title == "嵌套标题"
    assert resource.canonical_id == "douyin:9"


def test_non_dict_top_level_payload_is_stale(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)
    monkeypatch.setattr(adapter, "_run", FakeRunner(payload="just-a-string"))
    with pytest.raises(AdapterStale, match="unexpected top-level shape"):
        run(adapter.probe(canonicalize("https://v.douyin.com/x/")))


def test_registry_raises_when_every_adapter_fails(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    f2 = F2Adapter(config=config, name="f2", platforms=F2Adapter.platforms)
    monkeypatch.setattr(f2, "_which", lambda: None)

    class AlwaysFail(StubYtDlp):
        async def probe(self, url):
            raise AdapterError("yt-dlp blocked too")

    registry = AdapterRegistry([f2, AlwaysFail(config)])
    with pytest.raises(AdapterError, match="all adapters failed"):
        run(registry.probe(canonicalize("https://v.douyin.com/x/")))


def test_probe_no_metadata_403_is_sanitized_stale(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)

    async def blocked(args, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        return 0, "", "HTTP状态码错误： Status Code: 403 msToken=secret a_bogus=secret"

    monkeypatch.setattr(adapter, "_run", blocked)
    with pytest.raises(AdapterStale, match="HTTP 403") as exc:
        run(adapter.probe(canonicalize("https://v.douyin.com/x/")))
    assert "msToken" not in str(exc.value) and "a_bogus" not in str(exc.value)


def test_probe_no_metadata_cookie_block_is_sanitized_stale(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path)
    enable(adapter, monkeypatch)

    async def blocked(args, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        return 0, "", "cookie expired, please log in"

    monkeypatch.setattr(adapter, "_run", blocked)
    with pytest.raises(AdapterStale, match="cookie/login/signing block"):
        run(adapter.probe(canonicalize("https://v.douyin.com/x/")))