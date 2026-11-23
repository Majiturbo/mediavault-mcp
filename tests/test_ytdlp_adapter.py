"""yt-dlp adapter tests with the YoutubeDL client fully mocked (no network)."""

import asyncio
import json
from pathlib import Path

import pytest

from mediavault.adapters.base import AdapterError, AdapterStale
from mediavault.adapters.ytdlp_adapter import YtDlpAdapter, _slug
from mediavault.config import Config
from mediavault.models import Author, Resource
from mediavault.routing import canonicalize


def make_config(tmp_path: Path, **env) -> Config:
    payload = {"MEDIAVAULT_HOME": str(tmp_path / "vault")}
    payload.update(env)
    return Config.load(payload)


def make_adapter(tmp_path: Path, **env) -> YtDlpAdapter:
    cfg = make_config(tmp_path, **env)
    return YtDlpAdapter(
        name="yt-dlp",
        config=cfg,
        platforms=YtDlpAdapter.platforms,
        capabilities=YtDlpAdapter.capabilities,
    )


class FakeYDL:
    """Class-level scripted responses: the adapter builds its own instances,
    so state must be shared on the class rather than a hand-made instance."""

    instances: list["FakeYDL"] = []
    scripted_info: dict | None = None
    scripted_error: Exception | None = None

    def __init__(self, opts):
        self.opts = opts
        self.downloaded_urls: list[tuple[str, bool]] = []
        FakeYDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        if FakeYDL.scripted_error is not None:
            raise FakeYDL.scripted_error
        self.downloaded_urls.append((url, download))
        if download and FakeYDL.scripted_info is not None:
            out_dir = Path(self.opts["outtmpl"]).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{FakeYDL.scripted_info.get('title', 'clip')} [{FakeYDL.scripted_info['id']}]"
            (out_dir / f"{stem}.mp4").write_bytes(b"fake-mp4")
            (out_dir / f"{stem}.info.json").write_text("{}", encoding="utf-8")
        return FakeYDL.scripted_info


@pytest.fixture
def fake_ydl(monkeypatch):
    import yt_dlp

    FakeYDL.instances = []
    FakeYDL.scripted_info = None
    FakeYDL.scripted_error = None
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    return FakeYDL


def video_info(**overrides):
    info = {
        "id": "7412",
        "title": "测试视频 with weird chars",
        "extractor_key": "TikTok",
        "extractor": "tiktok",
        "webpage_url": "https://www.tiktok.com/@u/video/7412",
        "uploader": "alice",
        "channel_id": "uid1",
        "uploader_url": "https://tiktok.com/@u",
        "description": "caption text 100% done",
        "url": "https://cdn.example.com/master.mp4",
        "ext": "mp4",
        "vcodec": "h264",
        "acodec": "mp4a",
        "width": 1080,
        "height": 1920,
        "duration": 42.5,
        "filesize": 1024,
    }
    info.update(overrides)
    return info


def run(coro):
    return asyncio.run(coro)


def test_probe_maps_video_to_resource(tmp_path, fake_ydl):
    adapter = make_adapter(tmp_path)
    FakeYDL.scripted_info = video_info()

    resource = run(adapter.probe(canonicalize("https://www.tiktok.com/@u/video/7412")))

    assert resource.canonical_id == "tiktok:7412"
    assert resource.platform == "tiktok"
    assert resource.adapter_used == "yt-dlp"
    assert resource.title == "测试视频 with weird chars"
    assert resource.author.name == "alice"
    assert resource.author.id == "uid1"
    assert resource.caption == "caption text 100% done"
    item = resource.items[0]
    assert item.kind == "video"
    assert item.no_watermark == "native"  # tiktok is in the clean-source set
    assert item.width == 1080 and item.height == 1920
    assert item.duration_ms == 42500
    assert item.size_hint == 1024


def test_probe_unknown_platform_falls_back_to_extractor_name(tmp_path, fake_ydl):
    adapter = make_adapter(tmp_path)
    FakeYDL.scripted_info = video_info(extractor_key="Generic", extractor="generic", id="abc")
    resource = run(adapter.probe(canonicalize("https://example.site/watch/abc")))
    assert resource.platform == "generic"
    assert resource.canonical_id == "generic:abc"
    assert resource.items[0].no_watermark == "unknown"


def test_probe_audio_only_item(tmp_path, fake_ydl):
    adapter = make_adapter(tmp_path)
    FakeYDL.scripted_info = video_info(
        extractor_key="Youtube", extractor="youtube",
        vcodec="none", acodec="mp4a", ext="m4a", url="https://cdn/a.m4a",
    )
    resource = run(adapter.probe(canonicalize("https://youtu.be/7412")))
    assert resource.items[0].kind == "audio"


def test_probe_image_item_marked_native(tmp_path, fake_ydl):
    adapter = make_adapter(tmp_path)
    FakeYDL.scripted_info = video_info(
        extractor_key="Instagram", extractor="instagram",
        vcodec=None, acodec=None, ext="jpg", url="https://cdn/p.jpg",
    )
    resource = run(adapter.probe(canonicalize("https://www.instagram.com/p/x/")))
    item = resource.items[0]
    assert item.kind == "image"
    assert item.no_watermark == "native"


def test_probe_stale_markers_become_adapter_stale(tmp_path, fake_ydl):
    from yt_dlp.utils import DownloadError

    adapter = make_adapter(tmp_path)
    for marker in ("Unsupported URL: x", "login required or cookies needed"):
        FakeYDL.scripted_error = DownloadError(marker)
        with pytest.raises(AdapterStale):
            run(adapter.probe(canonicalize("https://v.douyin.com/x/")))
        FakeYDL.scripted_error = None


def test_probe_other_download_error_is_adapter_error(tmp_path, fake_ydl):
    from yt_dlp.utils import DownloadError

    adapter = make_adapter(tmp_path)
    FakeYDL.scripted_error = DownloadError("HTTP Error 429: Too Many Requests")
    with pytest.raises(AdapterError, match="429"):
        run(adapter.probe(canonicalize("https://youtu.be/x")))


def test_probe_empty_info_raises(tmp_path, fake_ydl):
    adapter = make_adapter(tmp_path)
    FakeYDL.scripted_info = None
    with pytest.raises(AdapterError, match="no metadata"):
        run(adapter.probe(canonicalize("https://youtu.be/x")))


def test_proxy_and_cookie_options_forwarded(tmp_path, fake_ydl):
    adapter = make_adapter(
        tmp_path,
        MEDIAVAULT_PROXY="http://127.0.0.1:7890",
        MEDIAVAULT_COOKIES_FILE="/tmp/cookies.txt",
    )
    FakeYDL.scripted_info = video_info()
    run(adapter.probe(canonicalize("https://www.tiktok.com/@u/video/7412")))
    opts = FakeYDL.instances[-1].opts
    assert opts["proxy"] == "http://127.0.0.1:7890"
    assert opts["cookiefile"] == "/tmp/cookies.txt"
    assert "cookiesfrombrowser" not in opts


def test_cookies_from_browser_forwarded(tmp_path, fake_ydl):
    adapter = make_adapter(tmp_path, MEDIAVAULT_COOKIES_BROWSER="chrome")
    FakeYDL.scripted_info = video_info()
    run(adapter.probe(canonicalize("https://www.tiktok.com/@u/video/7412")))
    opts = FakeYDL.instances[-1].opts
    assert opts["cookiesfrombrowser"] == ("chrome",)


def test_download_writes_resource_json_and_filters_info_json(tmp_path, fake_ydl):
    adapter = make_adapter(tmp_path)
    FakeYDL.scripted_info = video_info()

    resource = Resource(
        canonical_id="tiktok:7412",
        platform="tiktok",
        content_type="video",
        page_url="https://www.tiktok.com/@u/video/7412",
        title="测试视频",
        author=Author(name="alice"),
        items=[],
        adapter_used="yt-dlp",
    )
    files = run(adapter.download(resource, quality="speed"))

    assert len(files) == 1
    assert files[0].endswith(".mp4")
    assert not any(f.endswith(".info.json") for f in files)
    out_dir = Path(files[0]).parent
    meta = json.loads((out_dir / "resource.json").read_text(encoding="utf-8"))
    assert meta["canonical_id"] == "tiktok:7412"
    assert FakeYDL.instances[-1].downloaded_urls[-1] == (
        "https://www.tiktok.com/@u/video/7412", True,
    )
    assert "worstvideo+worstaudio" in FakeYDL.instances[-1].opts["format"]


def test_download_compat_format_and_output_layout(tmp_path, fake_ydl):
    adapter = make_adapter(tmp_path)
    FakeYDL.scripted_info = video_info()

    resource = Resource(
        canonical_id="youtube:ab", platform="youtube", content_type="video",
        page_url="https://youtu.be/ab", adapter_used="yt-dlp",
    )
    files = run(adapter.download(resource, quality="compat"))
    opts = FakeYDL.instances[-1].opts
    assert "1080" in opts["format"]
    assert opts["merged_output_format"] == "mp4"
    assert "/vault/youtube/ab/" in files[0]


def test_download_error_wrapped(tmp_path, fake_ydl):
    from yt_dlp.utils import DownloadError

    adapter = make_adapter(tmp_path)
    FakeYDL.scripted_error = DownloadError("boom")

    resource = Resource(
        canonical_id="x:1", platform="x", content_type="video",
        page_url="https://x.com/u/status/1", adapter_used="yt-dlp",
    )
    with pytest.raises(AdapterError, match="boom"):
        run(adapter.download(resource))


def test_probe_flattens_entries_and_skips_none(tmp_path, fake_ydl):
    adapter = make_adapter(tmp_path)
    FakeYDL.scripted_info = {
        "id": "pl1",
        "title": "carousel",
        "extractor_key": "TikTok",
        "extractor": "tiktok",
        "webpage_url": "https://www.tiktok.com/@u/video/pl1",
        "_type": "multi_video",
        "entries": [
            None,
            {"id": "e1", "ext": "mp4", "vcodec": "h264", "acodec": "aac",
             "url": "https://cdn/1.mp4", "duration": 10},
        ],
    }
    resource = run(adapter.probe(canonicalize("https://www.tiktok.com/@u/video/pl1")))
    assert len(resource.items) == 1
    assert resource.items[0].index == 1
    assert resource.items[0].kind == "video"


def test_download_backfills_item_local_path(tmp_path, fake_ydl):
    from mediavault.models import MediaItem

    adapter = make_adapter(tmp_path)
    FakeYDL.scripted_info = video_info()
    resource = Resource(
        canonical_id="tiktok:7412", platform="tiktok", content_type="video",
        page_url="https://www.tiktok.com/@u/video/7412", adapter_used="yt-dlp",
        items=[MediaItem(kind="video", url="https://cdn/master.mp4")],
    )
    files = run(adapter.download(resource))
    assert resource.items[0].local_path == files[0]


def test_slug_sanitizes_and_caps_length():
    assert _slug('a/b:c*d?e"f<g>h\\i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert _slug("") == "untitled"
    assert len(_slug("x" * 200)) == 80
