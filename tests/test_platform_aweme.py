"""Offline tests for the pure Douyin aweme_detail parser."""

import pytest

from mediavault.adapters.base import AdapterStale
from mediavault.platform_aweme import AWEME_DETAIL_JS, parse_aweme_detail

PAGE = "https://v.douyin.com/x/"


def test_parse_video_aweme():
    detail = {
        "aweme_id": "123",
        "desc": "教程",
        "author": {"sec_uid": "sec", "nickname": "博主"},
        "video": {
            "duration": 8000,
            "play_addr": {
                "url_list": ["https://cdn/1.mp4", "https://cdn/2.mp4"],
                "width": "bad", "height": 1280, "data_size": 555,
            },
        },
    }
    r = parse_aweme_detail(detail, PAGE, "browser")
    assert r.canonical_id == "douyin:123" and r.platform == "douyin"
    assert r.content_type == "video" and r.title == r.caption == "教程"
    assert r.author.name == "博主" and r.author.id == "sec"
    assert r.adapter_used == "browser"
    item = r.items[0]
    assert item.url == "https://cdn/1.mp4" and item.kind == "video"
    assert item.width is None and item.height == 1280  # non-int width ignored
    assert item.duration_ms == 8000 and item.size_hint == 555
    assert item.ext == "mp4" and item.no_watermark == "unknown"


def test_parse_image_set_aweme():
    detail = {
        "aweme_id": "9",
        "desc": "图文",
        "images": [
            {"url_list": ["https://cdn/a.jpg"]},
            {"url_list": []},  # no usable URL, skipped
            {"url_list": ["https://cdn/c.webp"]},
        ],
    }
    r = parse_aweme_detail(detail, PAGE, "browser")
    assert r.content_type == "image_set" and len(r.items) == 2
    assert [i.kind for i in r.items] == ["image", "image"]
    assert r.items[1].url == "https://cdn/c.webp" and r.author.name is None


def test_parse_mixed_keeps_video_content_type():
    detail = {
        "aweme_id": "7",
        "images": [{"url_list": ["https://cdn/a.jpg"]}],
        "video": {"play_addr": {"url_list": ["https://cdn/v.mp4"]}},
    }
    r = parse_aweme_detail(detail, PAGE, "browser")
    assert r.content_type == "video" and len(r.items) == 2
    assert r.items[-1].kind == "video"


@pytest.mark.parametrize("detail", [None, [], {}, {"aweme_id": None}, "x"])
def test_invalid_doc_is_stale(detail):
    with pytest.raises(AdapterStale, match="no aweme_detail"):
        parse_aweme_detail(detail, PAGE, "browser")


def test_doc_without_media_or_title_is_stale():
    with pytest.raises(AdapterStale, match="no recognizable media"):
        parse_aweme_detail({"aweme_id": "1", "video": {}}, PAGE, "browser")


def test_detail_js_is_page_script():
    assert "aweme/v1/web/aweme/detail" in AWEME_DETAIL_JS
    assert "credentials" in AWEME_DETAIL_JS