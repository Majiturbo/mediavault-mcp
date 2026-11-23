import pytest

from mediavault.routing import canonicalize, extract_urls, parse_input


def test_extract_urls_from_share_blob():
    text = "8.41 复制打开抖音，看看 https://v.douyin.com/iABCdef/ 测试，另一个 http://b23.tv/xyz"
    assert extract_urls(text) == ["https://v.douyin.com/iABCdef/", "http://b23.tv/xyz"]


def test_extract_strips_trailing_punctuation():
    assert extract_urls("看这个 https://x.com/a/status/123。") == ["https://x.com/a/status/123"]
    assert extract_urls("（https://youtu.be/x）") == ["https://youtu.be/x"]
    assert extract_urls('点这个 "https://v.kuaishou.com/ab", 快') == ["https://v.kuaishou.com/ab"]


def test_extract_dedupes_keeps_order():
    assert extract_urls("https://youtu.be/x https://youtu.be/x") == ["https://youtu.be/x"]
    text = "https://b23.tv/a https://b23.tv/b https://b23.tv/a"
    assert extract_urls(text) == ["https://b23.tv/a", "https://b23.tv/b"]


def test_extract_empty_and_non_http():
    assert extract_urls("") == []
    assert extract_urls("纯文字没有链接") == []
    assert extract_urls("ftp://example.com/file not-http") == []
    assert extract_urls("www.example.com/noscheme") == []


def test_extract_preserves_query_and_fragment():
    url = "https://www.xiaohongshu.com/discovery/item/abc?xsec_token=XYZ&type=video"
    assert extract_urls(url) == [url]


def test_extract_uppercase_scheme():
    assert extract_urls("HTTPS://WWW.YOUTUBE.COM/watch?v=1") == ["HTTPS://WWW.YOUTUBE.COM/watch?v=1"]


def test_extract_multiple_in_mixed_text():
    text = "第一个 https://tiktok.com/@u/video/1 和第二个 https://instagram.com/reel/x/ 结束"
    urls = extract_urls(text)
    assert len(urls) == 2
    assert urls[0].endswith("/1")
    assert urls[1].endswith("/x/")


PLATFORM_MATRIX = [
    ("https://v.douyin.com/x/", "douyin"),
    ("https://www.douyin.com/video/123", "douyin"),
    ("https://www.iesdouyin.com/share/video/1", "douyin"),
    ("https://xhslink.com/a/b", "xiaohongshu"),
    ("https://xhslink.cn/a", "xiaohongshu"),
    ("https://www.xiaohongshu.com/explore/1", "xiaohongshu"),
    ("https://v.kuaishou.com/abc", "kuaishou"),
    ("https://www.kuaishou.com/short-video/1", "kuaishou"),
    ("https://www.gifshow.com/fw/photo/1", "kuaishou"),
    ("https://m.weibo.cn/detail/1", "weibo"),
    ("https://weibo.com/123/abc", "weibo"),
    ("https://b23.tv/xyz", "bilibili"),
    ("https://www.bilibili.com/video/BV1xx", "bilibili"),
    ("https://vm.tiktok.com/abc/", "tiktok"),
    ("https://www.tiktok.com/@u/video/1", "tiktok"),
    ("https://www.instagram.com/reel/abc/", "instagram"),
    ("https://instagram.com/p/abc", "instagram"),
    ("https://x.com/u/status/1", "x"),
    ("https://twitter.com/u/status/1", "x"),
    ("https://www.youtube.com/watch?v=1", "youtube"),
    ("https://youtu.be/abc", "youtube"),
    ("https://fb.watch/abc/", "facebook"),
    ("https://vimeo.com/123", "vimeo"),
    ("https://www.reddit.com/r/x/comments/1/y/", "reddit"),
    ("https://v.redd.it/abc", "reddit"),
    ("https://pin.it/abc", "pinterest"),
    ("https://www.twitch.tv/v/1", "twitch"),
]


@pytest.mark.parametrize("url,platform", PLATFORM_MATRIX)
def test_platform_routing_matrix(url, platform):
    assert canonicalize(url).platform == platform


def test_unknown_platform_and_flag():
    cu = canonicalize("https://example.com/some/video")
    assert cu.platform == "unknown"
    assert cu.is_known_platform is False
    assert cu.host == "example.com"
    assert cu.path == "/some/video"


def test_subdomain_matching_does_not_overmatch():
    # evil lookalike domain must not be treated as douyin
    assert canonicalize("https://v.douyin.com.evil.com/x").platform == "unknown"
    assert canonicalize("https://notx.com/x").platform == "unknown"


def test_parse_input_preserves_order():
    parsed = parse_input("a https://youtu.be/1 b https://v.kuaishou.com/2")
    assert [u.platform for u in parsed] == ["youtube", "kuaishou"]
    assert all(u.raw_url for u in parsed)


def test_parse_input_raises_nothing_on_empty():
    assert parse_input("没有链接") == []
