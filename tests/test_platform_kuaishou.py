"""Offline tests for the pure Kuaishou Apollo photo parser."""

import pytest

from mediavault.adapters.base import AdapterStale
from mediavault.platform_kuaishou import KS_PHOTO_JS, parse_kuaishou_photo

PAGE = "https://www.kuaishou.com/short-video/abc"


def test_parse_string_photo_url():
    photo = {
        "photoId": "3x-photo",
        "caption": "今天吃什么",
        "duration": 73910,
        "width": 1080, "height": 1920,
        "photoUrl": "https://cdn/play.mp4",
        "author": {"id": "au1", "name": "快手博主"},
    }
    r = parse_kuaishou_photo(photo, PAGE, "browser")
    assert r.canonical_id == "kuaishou:3x-photo" and r.platform == "kuaishou"
    assert r.content_type == "video" and r.title == r.caption == "今天吃什么"
    assert r.author.name == "快手博主" and r.author.id == "au1"
    item = r.items[0]
    assert item.url == "https://cdn/play.mp4" and item.kind == "video"
    assert (item.width, item.height, item.duration_ms, item.ext) == (1080, 1920, 73910, "mp4")
    assert item.no_watermark == "unknown" and r.adapter_used == "browser"


def test_parse_manifest_picks_highest_bitrate():
    photo = {
        "photoId": "p2",
        "caption": "manifest",
        "duration": "bad",  # non-int duration ignored
        "photoUrl": {"adaptationSet": [
            {"representation": [
                {"url": "https://cdn/low.mp4", "bitrate": 500, "height": 720},
                {"url": "https://cdn/high.mp4", "bitrate": 2000, "height": 1920, "width": 1080},
            ]},
            {"__typename": "ignored"},  # non-dict adaptation skipped
        ]},
        "author": {"name": "n"},
    }
    r = parse_kuaishou_photo(photo, PAGE, "browser")
    item = r.items[0]
    assert item.url == "https://cdn/high.mp4" and item.height == 1920
    assert item.duration_ms is None and item.width == 1080


def test_manifest_without_bitrate_falls_back_to_first_rep():
    photo = {
        "photoId": "p3",
        "photoUrl": {"adaptationSet": [{"representation": [{"url": "https://cdn/a.mp4"}]}]},
    }
    r = parse_kuaishou_photo(photo, PAGE, "browser")
    assert r.items[0].url == "https://cdn/a.mp4"


@pytest.mark.parametrize("photo", [None, [], {}, {"photoId": None}, "x"])
def test_invalid_photo_is_stale(photo):
    with pytest.raises(AdapterStale, match="photo missing"):
        parse_kuaishou_photo(photo, PAGE, "browser")


def test_photo_without_playable_url_is_stale():
    # photoUrl missing entirely (None) -> non-dict manifest path returns empty
    with pytest.raises(AdapterStale, match="no playable video"):
        parse_kuaishou_photo({"photoId": "p4"}, PAGE, "browser")


def test_manifest_skips_reps_without_url():
    # all representations lack a string url -> no playable url -> stale
    photo = {
        "photoId": "p5",
        "photoUrl": {"adaptationSet": [
            "x",  # non-dict adaptation skipped
            {"representation": [{"bitrate": 1}, "y"]},  # no usable url
        ]},
    }
    with pytest.raises(AdapterStale, match="no playable video"):
        parse_kuaishou_photo(photo, PAGE, "browser")


def test_photo_js_targets_apollo_cache():
    assert "__APOLLO_STATE__" in KS_PHOTO_JS
    assert "VisionVideoDetailPhoto:" in KS_PHOTO_JS