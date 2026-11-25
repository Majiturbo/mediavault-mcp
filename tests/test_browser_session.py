"""Offline tests for the M4 browser session (in-page evaluate path).

Playwright is never launched: a fake async API (browser/context/page) is
injected into sys.modules. The missing-dependency path is exercised for real
because playwright is not part of the core test environment.
"""

import asyncio
import sys
import types

import pytest

from mediavault.browser_session import (
    BrowserChallengeError,
    BrowserUnavailable,
    PlaywrightBootstrapper,
    build_bootstrapper,
)
from mediavault.config import Config

CHALLENGE_HTML = '<html><script>var _$jsvmprt=function(){};</script></html>'
REAL_HTML = '<html><body><div id="_ROUTER_DATA">video</div></body></html>'
READY_COOKIES = [
    {"name": "ttwid", "value": "v", "domain": ".douyin.com"},
    {"name": "s_v_web_id", "value": "s", "domain": "www.douyin.com"},
]


def make_config(tmp_path, **over):
    env = {
        "MEDIAVAULT_HOME": str(tmp_path / "vault"),
        "MEDIAVAULT_BROWSER_BOOTSTRAP": "on",
        "MEDIAVAULT_BROWSER_TIMEOUT_MS": "5000",
    }
    env.update(over)
    return Config.load(env)


class FakePage:
    def __init__(self, content_sequence, fail_goto=False):
        self._sequence = list(content_sequence)
        self.fail_goto = fail_goto
        self.waits = []
        self.goto_kwargs = None

    async def goto(self, url, wait_until, timeout):
        self.goto_kwargs = {"url": url, "wait_until": wait_until, "timeout": timeout}
        if self.fail_goto:
            raise RuntimeError("goto boom")

    async def content(self):
        if len(self._sequence) > 1:
            return self._sequence.pop(0)
        return self._sequence[0]

    async def wait_for_timeout(self, ms):
        self.waits.append(ms)

    async def evaluate(self, expression, arg=None):
        self.evaluate_args = (expression, arg)
        return {"aweme_detail": {"aweme_id": "1"}}


class FakeContext:
    def __init__(self, page, cookie_sequence):
        self._page = page
        self._cookie_sequence = list(cookie_sequence)

    async def new_page(self):
        return self._page

    async def cookies(self):
        if len(self._cookie_sequence) > 1:
            return self._cookie_sequence.pop(0)
        return list(self._cookie_sequence[0])


class FakeBrowser:
    def __init__(self, context):
        self._context = context
        self.closed = False

    async def new_context(self):
        return self._context

    async def close(self):
        self.closed = True


class FakeEngine:
    def __init__(self, browser):
        self.browser = browser
        self.launch_kwargs = None

    async def launch(self, headless):
        self.launch_kwargs = {"headless": headless}
        return self.browser


class FakePlaywrightAPI:
    def __init__(self, engine):
        self.chromium = engine


class FakeRunner:
    def __init__(self, api):
        self.api = api

    async def __aenter__(self):
        return self.api

    async def __aexit__(self, *exc):
        return False


def install_fake_playwright(monkeypatch, page, cookie_sequence):
    browser = FakeBrowser(FakeContext(page, cookie_sequence))
    engine = FakeEngine(browser)
    api = FakePlaywrightAPI(engine)
    module = types.ModuleType("playwright.async_api")
    module.async_playwright = lambda: FakeRunner(api)
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)
    return browser, engine


def run(coro):
    return asyncio.run(coro)


def test_evaluate_after_ready_no_challenge(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    page = FakePage([REAL_HTML])
    browser, engine = install_fake_playwright(monkeypatch, page, [READY_COOKIES])
    result = run(PlaywrightBootstrapper(cfg).evaluate_after_ready(
        "douyin", "https://v.douyin.com/x/", "return 1", {"k": "v"}))
    assert result == {"aweme_detail": {"aweme_id": "1"}}
    assert page.evaluate_args == ("return 1", {"k": "v"})
    assert engine.launch_kwargs == {"headless": True}
    assert page.goto_kwargs["wait_until"] == "domcontentloaded"
    assert page.waits and browser.closed is True


def test_evaluate_after_ready_challenge_then_pass(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    page = FakePage([CHALLENGE_HTML, REAL_HTML])
    browser, _ = install_fake_playwright(monkeypatch, page, [READY_COOKIES])
    result = run(PlaywrightBootstrapper(cfg).evaluate_after_ready(
        "douyin", "https://v.douyin.com/x/", "return 1"))
    assert result["aweme_detail"]["aweme_id"] == "1"
    assert page.waits and browser.closed is True  # challenge polled, then passed


def test_douyin_waits_for_readiness_cookie(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    page = FakePage([REAL_HTML])
    partial = [{"name": "ttwid", "value": "v", "domain": ".douyin.com"}]
    install_fake_playwright(monkeypatch, page, [partial, READY_COOKIES])
    result = run(PlaywrightBootstrapper(cfg).evaluate_after_ready(
        "douyin", "https://v.douyin.com/x/", "return 1"))
    assert result["aweme_detail"]["aweme_id"] == "1" and page.waits


def test_platform_without_readiness_gate(tmp_path, monkeypatch):
    # kuaishou has no readiness-cookie gate yet: any stable non-empty set passes
    cfg = make_config(tmp_path)
    page = FakePage([REAL_HTML])
    cookies = [{"name": "did", "value": "v", "domain": ".kuaishou.com"}]
    install_fake_playwright(monkeypatch, page, [cookies])
    result = run(PlaywrightBootstrapper(cfg).evaluate_after_ready(
        "kuaishou", "https://www.kuaishou.com/short-video/x", "return 1"))
    assert result["aweme_detail"]["aweme_id"] == "1"


def test_xiaohongshu_waits_for_a1_cookie(tmp_path, monkeypatch):
    # first sample lacks the anonymous a1 device cookie; once it appears and
    # the set stabilizes, evaluation proceeds
    cfg = make_config(tmp_path)
    page = FakePage([REAL_HTML])
    partial = [{"name": "webId", "value": "w", "domain": ".xiaohongshu.com"}]
    ready = partial + [
        {"name": "a1", "value": "device", "domain": ".xiaohongshu.com"},
    ]
    install_fake_playwright(monkeypatch, page, [partial, ready])
    result = run(PlaywrightBootstrapper(cfg).evaluate_after_ready(
        "xiaohongshu", "https://www.xiaohongshu.com/explore/x", "return 1"))
    assert result["aweme_detail"]["aweme_id"] == "1" and page.waits


def test_timeout_raises_challenge_error(tmp_path, monkeypatch):
    cfg = make_config(tmp_path, **{"MEDIAVAULT_BROWSER_TIMEOUT_MS": "0"})
    page = FakePage([CHALLENGE_HTML])
    browser, _ = install_fake_playwright(monkeypatch, page, [READY_COOKIES])
    with pytest.raises(BrowserChallengeError, match="not cleared"):
        run(PlaywrightBootstrapper(cfg).evaluate_after_ready(
            "douyin", "https://v.douyin.com/x/", "return 1"))
    assert browser.closed is True


def test_goto_failure_becomes_unavailable(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    page = FakePage([REAL_HTML], fail_goto=True)
    install_fake_playwright(monkeypatch, page, [READY_COOKIES])
    with pytest.raises(BrowserUnavailable, match="local browser failed: goto boom"):
        run(PlaywrightBootstrapper(cfg).evaluate_after_ready(
            "douyin", "https://v.douyin.com/x/", "return 1"))


def test_missing_playwright_raises_unavailable(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    monkeypatch.delitem(sys.modules, "playwright.async_api", raising=False)
    monkeypatch.setattr(sys, "path", [])
    with pytest.raises(BrowserUnavailable, match="playwright is not installed"):
        run(PlaywrightBootstrapper(cfg).evaluate_after_ready(
            "douyin", "https://v.douyin.com/x/", "return 1"))


def test_build_bootstrapper_switch(tmp_path):
    on = make_config(tmp_path)
    assert isinstance(build_bootstrapper(on), PlaywrightBootstrapper)
    off = make_config(tmp_path, **{"MEDIAVAULT_BROWSER_BOOTSTRAP": "off"})
    assert build_bootstrapper(off) is None