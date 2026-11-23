"""URL extraction from share text and platform routing.

Users paste whole share blobs ("8.41 复制打开抖音，看看... https://v.douyin.com/x/"),
not clean URLs. Extraction is regex-based and deliberately conservative;
platform identification then uses ordered host suffix rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# http(s) URL, stop at whitespace and CJK punctuation commonly pasted around it
_URL_RE = re.compile(r'https?://[^\s，。；、）)】」』"\'<>]+', re.IGNORECASE)


@dataclass(frozen=True)
class CanonicalURL:
    raw_url: str
    platform: str  # "unknown" when no host rule matches; generic adapters still try
    host: str
    path: str

    @property
    def is_known_platform(self) -> bool:
        return self.platform != "unknown"


# host suffix -> platform id. Longest/most specific first where overlap exists.
_HOST_RULES: list[tuple[str, str]] = [
    # domestic
    ("v.douyin.com", "douyin"),
    ("www.douyin.com", "douyin"),
    ("douyin.com", "douyin"),
    ("iesdouyin.com", "douyin"),
    ("xhslink.com", "xiaohongshu"),
    ("xhslink.cn", "xiaohongshu"),
    ("www.xiaohongshu.com", "xiaohongshu"),
    ("xiaohongshu.com", "xiaohongshu"),
    ("v.kuaishou.com", "kuaishou"),
    ("www.kuaishou.com", "kuaishou"),
    ("kuaishou.com", "kuaishou"),
    ("gifshow.com", "kuaishou"),
    ("chenzhongtech.com", "kuaishou"),
    ("m.weibo.cn", "weibo"),
    ("weibo.cn", "weibo"),
    ("weibo.com", "weibo"),
    ("b23.tv", "bilibili"),
    ("www.bilibili.com", "bilibili"),
    ("bilibili.com", "bilibili"),
    # overseas
    ("vm.tiktok.com", "tiktok"),
    ("vt.tiktok.com", "tiktok"),
    ("www.tiktok.com", "tiktok"),
    ("tiktok.com", "tiktok"),
    ("www.instagram.com", "instagram"),
    ("instagram.com", "instagram"),
    ("x.com", "x"),
    ("twitter.com", "x"),
    ("www.youtube.com", "youtube"),
    ("youtube.com", "youtube"),
    ("youtu.be", "youtube"),
    ("www.facebook.com", "facebook"),
    ("facebook.com", "facebook"),
    ("fb.watch", "facebook"),
    ("vimeo.com", "vimeo"),
    ("www.reddit.com", "reddit"),
    ("reddit.com", "reddit"),
    ("v.redd.it", "reddit"),
    ("pin.it", "pinterest"),
    ("www.pinterest.com", "pinterest"),
    ("pinterest.com", "pinterest"),
    ("www.twitch.tv", "twitch"),
    ("twitch.tv", "twitch"),
]


def extract_urls(text: str) -> list[str]:
    """Return every http(s) URL found in a share blob, de-duplicated, order kept."""
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;!?")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _match_platform(host: str) -> str:
    host = host.lower()
    for suffix, platform in _HOST_RULES:
        if host == suffix or host.endswith("." + suffix):
            return platform
    return "unknown"


def canonicalize(url: str) -> CanonicalURL:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return CanonicalURL(raw_url=url, platform=_match_platform(host), host=host, path=parts.path)


def parse_input(text: str) -> list[CanonicalURL]:
    """Extract and canonicalize all URLs from pasted text or a bare URL."""
    return [canonicalize(u) for u in extract_urls(text)]
