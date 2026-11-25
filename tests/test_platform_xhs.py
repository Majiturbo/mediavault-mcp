"""Offline tests for the pure Xiaohongshu note parser."""

import pytest

from mediavault.adapters.base import AdapterStale
from mediavault.platform_xhs import XHS_NOTE_JS, parse_xhs_note

PAGE = "https://www.xiaohongshu.com/explore/abc?xsec_token=t"


def test_parse_image_note():
    note = {
        "noteId": "6affb9ba",
        "type": "normal",
        "title": "广州胶片写真",
        "desc": "第一行\n第二行",
        "user": {"userId": "u1", "nickname": "摄影师"},
        "imageList": [
            {"urlDefault": "https://ci/a.jpg?imageView", "width": 1080, "height": 1440},
            {"urlDefault": "https://ci/b.jpg"},
            {"url": "https://ci/fallback.jpg"},  # urlDefault missing, url fallback
            {},  # dict without any usable URL, skipped
            "not-a-dict",  # ignored
        ],
    }
    r = parse_xhs_note(note, PAGE, "browser")
    assert r.canonical_id == "xhs:6affb9ba" and r.platform == "xiaohongshu"
    assert r.content_type == "image_set" and r.title == "广州胶片写真"
    assert r.caption == "第一行\n第二行"
    assert r.author.name == "摄影师" and r.author.id == "u1"
    assert len(r.items) == 3
    assert r.items[0].kind == "image" and r.items[0].width == 1080
    assert r.items[0].ext == "jpg" and r.items[0].no_watermark == "unknown"
    assert r.items[2].url.endswith("fallback.jpg")
    assert r.adapter_used == "browser"


def test_parse_video_note_prefers_h264():
    note = {
        "noteId": "6af1a89b",
        "title": "",  # empty title falls back to first desc line
        "desc": "张萌穿搭",
        "user": {"nickname": "剧评"},
        "imageList": [{"urlDefault": "https://ci/cover.jpg"}],
        "video": {"media": {"stream": {
            "h265": [{"masterUrl": "https://v/h265.m3u8"}],
            "h264": [{"masterUrl": "https://v/play.mp4", "width": 720, "height": 1280}],
        }}},
    }
    r = parse_xhs_note(note, PAGE, "browser")
    assert r.content_type == "video" and r.title == "张萌穿搭"
    video = next(i for i in r.items if i.kind == "video")
    assert video.url == "https://v/play.mp4" and video.ext == "mp4"
    assert (video.width, video.height) == (720, 1280)
    assert len(r.items) == 2  # cover image plus video
    assert r.author.id is None


def test_video_note_without_h264_skips_video_keeps_images():
    note = {
        "noteId": "n1",
        "imageList": [{"urlDefault": "https://ci/a.jpg"}],
        "video": {"media": {"stream": {"h265": [{"masterUrl": "x"}]}}},
    }
    r = parse_xhs_note(note, PAGE, "browser")
    assert r.content_type == "image_set" and len(r.items) == 1


@pytest.mark.parametrize("note", [None, [], {}, {"noteId": None}, "x"])
def test_invalid_note_is_stale(note):
    with pytest.raises(AdapterStale, match="note missing"):
        parse_xhs_note(note, PAGE, "browser")


def test_note_without_media_is_stale():
    with pytest.raises(AdapterStale, match="no recognizable"):
        parse_xhs_note({"noteId": "n2", "title": "empty"}, PAGE, "browser")


def test_note_js_reads_initial_state():
    assert "__INITIAL_STATE__" in XHS_NOTE_JS
    assert "noteDetailMap" in XHS_NOTE_JS