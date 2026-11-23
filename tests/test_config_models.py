import json
from pathlib import Path

from mediavault.config import Config
from mediavault.models import Author, DownloadResult, MediaItem, Resource


def write_env(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_config_defaults_with_clean_env(tmp_path: Path):
    cfg = Config.load({"MEDIAVAULT_HOME": str(tmp_path / "vault")})
    assert cfg.home == tmp_path / "vault"
    assert cfg.proxy is None
    assert cfg.cookies_browser is None
    assert cfg.cookies_file is None
    assert cfg.openai_base_url == "https://api.openai.com/v1"
    assert cfg.openai_api_key is None
    assert cfg.llm_model == "gpt-4o-mini"
    assert cfg.vlm_model == "gpt-4o-mini"


def test_config_env_beats_dotenv(tmp_path: Path, monkeypatch):
    write_env(
        tmp_path / ".env",
        """MEDIAVAULT_PROXY=http://from-dotenv:7890
OPENAI_API_KEY=dotenv-key
""",
    )
    monkeypatch.chdir(tmp_path)
    cfg = Config.load({
        "MEDIAVAULT_HOME": str(tmp_path / "vault"),
        "MEDIAVAULT_PROXY": "http://from-env:1080",
    })
    assert cfg.proxy == "http://from-env:1080"
    # key only present in dotenv still loads
    assert cfg.openai_api_key == "dotenv-key"


def test_config_dotenv_quotes_comments_and_export(tmp_path: Path, monkeypatch):
    write_env(tmp_path / ".env", '''# a comment
export OPENAI_API_KEY="sk-quoted"
MEDIAVAULT_COOKIES_BROWSER='chrome'
MEDIAVAULT_LLM_MODEL=custom-model
''')
    monkeypatch.chdir(tmp_path)
    cfg = Config.load({"MEDIAVAULT_HOME": str(tmp_path / "vault")})
    assert cfg.openai_api_key == "sk-quoted"
    assert cfg.cookies_browser == "chrome"
    assert cfg.llm_model == "custom-model"


def test_config_home_dotenv_also_reads(tmp_path: Path, monkeypatch):
    home = tmp_path / "vault"
    home.mkdir()
    write_env(home / ".env", "MEDIAVAULT_PROXY=http://home-dotenv:1\n")
    monkeypatch.chdir(tmp_path)  # cwd has no .env here
    cfg = Config.load({"MEDIAVAULT_HOME": str(home)})
    assert cfg.proxy == "http://home-dotenv:1"


def test_config_empty_env_value_falls_through(tmp_path: Path):
    cfg = Config.load({"MEDIAVAULT_HOME": str(tmp_path / "vault"), "MEDIAVAULT_PROXY": ""})
    assert cfg.proxy is None


def test_config_custom_openai_base(tmp_path: Path):
    cfg = Config.load({
        "MEDIAVAULT_HOME": str(tmp_path / "v"),
        "OPENAI_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
        "OPENAI_API_KEY": "ark-key",
        "MEDIAVAULT_VLM_MODEL": "qwen-vl",
    })
    assert cfg.openai_base_url == "https://ark.cn-beijing.volces.com/api/v3"
    assert cfg.openai_api_key == "ark-key"
    assert cfg.vlm_model == "qwen-vl"


def test_ensure_home_creates_directory(tmp_path: Path):
    target = tmp_path / "nested" / "MediaVault"
    cfg = Config.load({"MEDIAVAULT_HOME": str(target)})
    assert not target.exists()
    created = cfg.ensure_home()
    assert created == target
    assert target.is_dir()
    # idempotent
    cfg.ensure_home()


def test_resource_round_trips_through_json():
    resource = Resource(
        canonical_id="douyin:123",
        platform="douyin",
        content_type="video",
        page_url="https://v.douyin.com/x/",
        title="t",
        author=Author(name="alice", id="uid1", url="https://x/u"),
        caption="文案 100%",
        items=[
            MediaItem(
                kind="video", url="https://cdn/x.mp4",
                no_watermark="native", height=1920,
            )
        ],
        adapter_used="yt-dlp",
        fallback_chain=["f2", "yt-dlp"],
    )
    data = json.loads(json.dumps(resource.to_dict(), ensure_ascii=False))
    assert data["canonical_id"] == "douyin:123"
    assert data["items"][0]["no_watermark"] == "native"
    assert data["items"][0]["height"] == 1920
    assert data["author"]["name"] == "alice"
    assert data["caption"] == "文案 100%"
    assert data["fallback_chain"] == ["f2", "yt-dlp"]


def test_resource_defaults_and_download_result():
    resource = Resource(canonical_id="x:1", platform="x", content_type="image_set", page_url="u")
    assert resource.items == []
    assert resource.degraded is False
    assert resource.fetched_at  # timestamp generated
    result = DownloadResult(resource=resource, files=["/tmp/a.jpg"], output_dir="/tmp")
    payload = result.to_dict()
    assert payload["files"] == ["/tmp/a.jpg"]
    assert payload["resource"]["canonical_id"] == "x:1"


def test_media_item_default_watermark_is_unknown():
    item = MediaItem(kind="video")
    assert item.no_watermark == "unknown"
    assert item.local_path is None
