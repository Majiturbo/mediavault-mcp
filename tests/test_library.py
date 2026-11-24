"""Offline tests for the on-disk media library cache."""

from mediavault.config import Config
from mediavault.library import EVIDENCE_FILE, SUMMARY_FILE, MediaLibrary, split_canonical


def make_library(tmp_path):
    cfg = Config.load({"MEDIAVAULT_HOME": str(tmp_path / "vault")})
    return MediaLibrary(cfg), cfg


def sample_payload(canonical_id="douyin:7401", frames=None):
    return {
        "canonical_id": canonical_id,
        "platform": canonical_id.split(":", 1)[0],
        "title": "缓存测试",
        "frames_extracted": len(frames or []),
        "frame_paths": frames or [],
        "pipeline": {"transcript": "ok", "keyframes": "extracted", "errors": []},
    }


def test_split_canonical_variants():
    assert split_canonical("douyin:abc:123") == ("douyin", "abc:123")
    assert split_canonical("plain") == ("unknown", "plain")
    assert split_canonical(":onlyid") == ("unknown", "onlyid")
    assert split_canonical("x:") == ("x", "unknown")


def test_resource_dir_uses_platform_and_slug(tmp_path):
    library, _ = make_library(tmp_path)
    path = library.resource_dir("douyin:7401")
    assert path.name == "7401"
    assert path.parent.name == "douyin"
    # unsafe characters are neutralized
    weird = library.resource_dir("x:a/b\\c?")
    assert "/" not in weird.name and "?" not in weird.name


def test_save_and_load_evidence_roundtrip(tmp_path):
    library, _ = make_library(tmp_path)
    payload = sample_payload()
    path = library.save_evidence("douyin:7401", payload)

    assert path.name == EVIDENCE_FILE
    assert path.is_file()
    loaded = library.load_evidence("douyin:7401")
    assert loaded["title"] == "缓存测试"
    # written as pretty JSON, UTF-8 preserved
    raw = path.read_text(encoding="utf-8")
    assert "缓存测试" in raw and "\n" in raw


def test_load_missing_and_corrupt_returns_none(tmp_path):
    library, _ = make_library(tmp_path)
    assert library.load_evidence("douyin:missing") is None

    bad_path = library.evidence_path("douyin:broken")
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text("{not valid json", encoding="utf-8")
    assert library.load_evidence("douyin:broken") is None

    # non-dict JSON is also treated as a miss
    list_path = library.evidence_path("douyin:aslist")
    list_path.parent.mkdir(parents=True, exist_ok=True)
    list_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert library.load_evidence("douyin:aslist") is None


def test_atomic_write_leaves_no_temp_file(tmp_path):
    library, _ = make_library(tmp_path)
    library.save_evidence("douyin:1", sample_payload("douyin:1"))
    resource_dir = library.resource_dir("douyin:1")
    assert not list(resource_dir.glob("*.tmp"))


def test_reusable_evidence_requires_existing_frames_for_media(tmp_path):
    library, _ = make_library(tmp_path)
    frame = tmp_path / "vault" / "douyin" / "7401" / "keyframes" / "frame_001.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"jpeg")
    library.save_evidence("douyin:7401", sample_payload(frames=[str(frame)]))

    # metadata-only request accepts the cache even without checking frames
    cached = library.reusable_evidence("douyin:7401", with_media=False)
    assert cached is not None and cached["cached"] is True

    # media request accepts while the frame file exists
    cached_media = library.reusable_evidence("douyin:7401", with_media=True)
    assert cached_media is not None and cached_media["cached"] is True

    frame.unlink()
    assert library.reusable_evidence("douyin:7401", with_media=True) is None
    # metadata-only still hits
    assert library.reusable_evidence("douyin:7401", with_media=False) is not None


def test_reusable_evidence_empty_frames_cannot_back_media_request(tmp_path):
    library, _ = make_library(tmp_path)
    library.save_evidence("douyin:cap", sample_payload(canonical_id="douyin:cap"))
    assert library.reusable_evidence("douyin:cap", with_media=True) is None
    assert library.reusable_evidence("douyin:cap", with_media=False) is not None


def test_summary_save_and_load(tmp_path):
    library, _ = make_library(tmp_path)
    library.save_summary("douyin:1", {"tldr": "要点", "pipeline": {}})
    assert library.summary_path("douyin:1").name == SUMMARY_FILE
    assert library.load_summary("douyin:1")["tldr"] == "要点"
    assert library.load_summary("douyin:absent") is None


def test_index_lists_cached_resources_and_summary_flag(tmp_path):
    library, _ = make_library(tmp_path)
    library.save_evidence("douyin:a", sample_payload("douyin:a"))
    library.save_evidence("xhs:b", sample_payload("xhs:b", frames=["/x.jpg"]))
    library.save_summary("douyin:a", {"tldr": "s"})

    index = library.index()
    assert len(index) == 2
    by_id = {entry["canonical_id"]: entry for entry in index}
    assert by_id["douyin:a"]["has_summary"] is True
    assert by_id["xhs:b"]["has_summary"] is False
    assert by_id["xhs:b"]["frames_extracted"] == 1
    assert all(entry["path"] for entry in index)


def test_index_empty_home_returns_empty_list(tmp_path):
    library, _ = make_library(tmp_path)
    assert library.index() == []


def test_index_skips_corrupt_evidence(tmp_path):
    library, _ = make_library(tmp_path)
    library.save_evidence("douyin:good", sample_payload("douyin:good"))
    bad_dir = library.resource_dir("douyin:bad")
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / EVIDENCE_FILE).write_text("{", encoding="utf-8")

    index = library.index()
    assert [e["canonical_id"] for e in index] == ["douyin:good"]
