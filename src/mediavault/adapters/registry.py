"""Adapter registry with ordered fallback chains.

The orchestration layer never names a concrete backend. It asks the registry
for the chain of adapters that claim a platform, walks it in score order, and
treats AdapterStale as "try the next one" while other AdapterError values are
terminal for that adapter but still allow fallback.
"""

from __future__ import annotations

import logging

from mediavault.adapters.base import Adapter, AdapterError, AdapterStale
from mediavault.adapters.browser_adapter import BrowserYtDlpAdapter
from mediavault.adapters.f2_adapter import F2Adapter
from mediavault.adapters.gallerydl_adapter import GalleryDLAdapter
from mediavault.adapters.ssr_adapter import SsrAdapter
from mediavault.adapters.ytdlp_adapter import YtDlpAdapter
from mediavault.config import Config
from mediavault.models import Resource
from mediavault.routing import CanonicalURL

logger = logging.getLogger("mediavault.registry")


class AdapterRegistry:
    def __init__(self, adapters: list[Adapter] | None = None) -> None:
        self._adapters: list[Adapter] = list(adapters or [])

    def register(self, adapter: Adapter) -> None:
        self._adapters.append(adapter)

    def chain_for(self, url: CanonicalURL) -> list[Adapter]:
        candidates = [a for a in self._adapters if a.supports(url)]
        return sorted(candidates, key=lambda a: a.match_score(url), reverse=True)

    async def probe(self, url: CanonicalURL) -> Resource:
        chain = self.chain_for(url)
        if not chain:
            raise AdapterError(f"no adapter registered for platform={url.platform!r}")

        failures: list[str] = []
        for adapter in chain:
            try:
                resource = await adapter.probe(url)
                resource.fallback_chain = [a.name for a in chain]
                if failures:
                    resource.degraded = True
                    resource.notes.extend(failures)
                return resource
            except AdapterStale as exc:
                logger.warning("adapter %s stale for %s: %s", adapter.name, url.raw_url, exc)
                failures.append(f"{adapter.name}: stale ({exc})")
            except AdapterError as exc:
                logger.warning("adapter %s failed for %s: %s", adapter.name, url.raw_url, exc)
                failures.append(f"{adapter.name}: {exc}")
        raise AdapterError("all adapters failed: " + " | ".join(failures))


def build_default_registry(config: Config) -> AdapterRegistry:
    # f2 ranks first for the CN platforms it tracks (match_score 200); when
    # its binary is absent or a page shape drifts it raises AdapterStale and the
    # registry transparently falls through to yt-dlp, the generic 1800+ site
    # workhorse. Class attributes are passed explicitly because the dataclass
    # __init__ would otherwise shadow them with the base field defaults.
    adapters: list[Adapter] = []
    # Opt-in M4 browser bootstrap ranks above every CLI adapter for the
    # JS-VM challenge platforms; absent Playwright degrades via AdapterStale.
    if config.browser_bootstrap == "on":
        adapters.append(BrowserYtDlpAdapter(
            config=config,
            name="browser",
            platforms=BrowserYtDlpAdapter.platforms,
            capabilities=BrowserYtDlpAdapter.capabilities,
        ))
    # Binary-free SSR fetch (score 175) sits below f2 (200) and above
    # gallery-dl (150); it only claims Xiaohongshu/Kuaishou and degrades via
    # AdapterStale when the document carries no hydrated detail state.
    adapters.append(SsrAdapter(
        config=config,
        name="ssr",
        platforms=SsrAdapter.platforms,
        capabilities=SsrAdapter.capabilities,
    ))
    adapters.extend([
        F2Adapter(
            config=config,
            name="f2",
            platforms=F2Adapter.platforms,
            capabilities=F2Adapter.capabilities,
        ),
        GalleryDLAdapter(
            config=config,
            name="gallery-dl",
            platforms=GalleryDLAdapter.platforms,
            capabilities=GalleryDLAdapter.capabilities,
        ),
        YtDlpAdapter(
            config=config,
            name="yt-dlp",
            platforms=YtDlpAdapter.platforms,
            capabilities=YtDlpAdapter.capabilities,
        ),
    ])
    return AdapterRegistry(adapters)
