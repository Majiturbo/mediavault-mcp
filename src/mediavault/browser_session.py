"""Local real-browser session for JS-VM challenge platforms (M4).

Some CN platforms answer non-browser HTTP clients with a JavaScript VM
challenge (an empty body holding an obfuscated bootstrap script) instead of
the page. A real browser executes that challenge and the in-page fetch then
carries the ambient fingerprint, signing headers and cookies that authorize
platform APIs. We never log in and never export cookie values; the session
lives only inside the browser context and is closed when the call returns.

Playwright is an optional dependency imported lazily. Its absence raises
BrowserUnavailable; the adapter maps that to AdapterStale so the normal
fallback chain keeps working with no browser installed.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

from mediavault.config import Config

# Marker inside the JS-VM anti-bot wall document (observed on Douyin). The
# wall body is empty and only carries this obfuscated bootstrap script, so
# its presence is a reliable "still challenged" signal across platforms.
_CHALLENGE_MARKER = "_$jsvmprt"
_POLL_INTERVAL_MS = 500
# A decisive session cookie planted several seconds after domcontentloaded.
# Waiting for it (plus a stable cookie set) avoids acting on a partial
# session before the SPA finished its anonymous bootstrap.
_READINESS_COOKIES: dict[str, frozenset[str]] = {
    "douyin": frozenset({"s_v_web_id"}),
    # Xiaohongshu plants the anonymous device id "a1" on first paint; waiting
    # for it avoids reading __INITIAL_STATE__ before the SPA hydrates.
    "xiaohongshu": frozenset({"a1"}),
    # Kuaishou sets the anonymous device id "did" before the Apollo cache
    # hydrates; gating on it avoids reading an unhydrated short-video page.
    "kuaishou": frozenset({"did"}),
}
_INSTALL_HINT = 'install the extra with: pip install "mediavault-mcp[browser]"'
# Real desktop Chrome UA. The headless default UA contains "HeadlessChrome",
# which CN risk control rejects outright; the context must present a normal UA.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# Applied before any page script runs. Live tests showed Xiaohongshu/Kuaishou
# edge risk control rejects a context with navigator.webdriver=true and an empty
# plugin list even on a clean residential IP; masking those automation signals
# (plus a real UA, and headed mode where required) lets the anonymous page
# hydrate. This is fingerprint normalisation, not account login automation.
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
window.chrome = {runtime: {}};
"""


class BrowserUnavailable(RuntimeError):
    """Playwright is not installed or the local browser cannot launch."""


class BrowserChallengeError(RuntimeError):
    """The browser could not clear the JS challenge within the timeout."""


class PlaywrightBootstrapper:
    """Launch a local browser, clear the JS challenge, run a page script."""

    name = "playwright-local-browser"

    def __init__(self, config: Config) -> None:
        self.config = config

    def _import_playwright(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserUnavailable(
                f"playwright is not installed; {_INSTALL_HINT}"
            ) from exc
        return async_playwright

    async def _launch(self, playwright_api):
        engine = getattr(playwright_api, self.config.browser_engine)
        return await engine.launch(headless=self.config.browser_headless)

    async def _wait_until_passed(self, page, context, platform: str) -> bool:
        """Poll until the wall document is replaced and the session is ready."""
        deadline = time.monotonic() + self.config.browser_timeout_ms / 1000
        required = _READINESS_COOKIES.get(platform, frozenset())
        challenge_seen = False
        previous: frozenset[tuple[str, str]] | None = None
        while True:
            content = await page.content()
            if _CHALLENGE_MARKER in content:
                challenge_seen = True
                previous = None
            else:
                cookies = await context.cookies()
                names = {str(c.get("name") or "") for c in cookies}
                current = frozenset(
                    (str(c.get("name") or ""), str(c.get("domain") or ""))
                    for c in cookies
                )
                has_required = required.issubset(names)
                if cookies and has_required and current == previous:
                    return challenge_seen
                previous = current
            if time.monotonic() >= deadline:
                raise BrowserChallengeError(
                    "JS challenge was not cleared within "
                    f"{self.config.browser_timeout_ms} ms"
                )
            await page.wait_for_timeout(_POLL_INTERVAL_MS)

    @asynccontextmanager
    async def _open_page(self, platform: str, url: str):
        """Launch, navigate and wait for readiness; always close the browser."""
        async_playwright = self._import_playwright()
        runner = async_playwright()
        try:
            async with runner as api:
                browser = await self._launch(api)
                try:
                    context = await browser.new_context(user_agent=_BROWSER_UA)
                    await context.add_init_script(_STEALTH_JS)
                    page = await context.new_page()
                    await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=self.config.browser_timeout_ms,
                    )
                    await self._wait_until_passed(page, context, platform)
                    yield page
                finally:
                    await browser.close()
        except (BrowserUnavailable, BrowserChallengeError):
            raise
        except Exception as exc:
            raise BrowserUnavailable(f"local browser failed: {exc}") from exc

    async def evaluate_after_ready(
        self, platform: str, url: str, expression: str, arg: Any = None
    ) -> Any:
        """Run a page-context script after the session is challenge-free."""
        async with self._open_page(platform, url) as page:
            return await page.evaluate(expression, arg)


def build_bootstrapper(config: Config) -> PlaywrightBootstrapper | None:
    """Factory honoring the opt-in switch; off means no browser can launch."""
    if config.browser_bootstrap != "on":
        return None
    return PlaywrightBootstrapper(config)