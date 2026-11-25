# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and the project
adheres to semantic versioning once 1.0.0 is tagged.

## Unreleased

### Fixed
- Calibrated the optional f2 adapter for f2 CLI 0.0.1.x: `-M` is now
  `--mode` and requires `one` for a single work, and proxies use `-P` instead
  of the old `--proxy` form. Empty f2 runs on HTTP 403 now report a sanitized
  stale marker without leaking msToken/a_bogus request parameters.

### Added
- **M4 browser-backed challenge bootstrap (opt-in, anonymous):**
  - New `browser` extra (`pip install "mediavault-mcp[browser]"` plus
    `playwright install chromium`) and `MEDIAVAULT_BROWSER_BOOTSTRAP=on`.
  - Live testing proved Douyin rejects even a complete cookie jar when the
    request comes from a non-browser client (yt-dlp/f2), but authorizes the
    same detail API inside a real page. The new adapter therefore launches a
    local Playwright browser, clears the JS-VM challenge anonymously (no
    login), runs an in-page `fetch` of the detail API, parses `aweme_detail`
    into a `Resource` via a pure, offline-tested parser, and streams the signed
    CDN URL directly over httpx. Only cookie names are ever logged.
  - A readiness gate waits for the decisive `s_v_web_id` session cookie (Douyin),
    anonymous `a1` device cookie (Xiaohongshu) or `did` device cookie (Kuaishou)
    and a stable cookie set before acting, so a partial early session is never used.
  - Platform dispatch maps Douyin (`aweme_detail`), Xiaohongshu (hydrated
    `__INITIAL_STATE__` note, images and h264 video) and Kuaishou (Apollo
    `VisionVideoDetailPhoto` cache, direct `photoUrl` or highest-bitrate manifest
    representation) through pure, offline-tested parsers. On Xiaohongshu network
    risk control (`300012`) or a Kuaishou bare `{"result": 2}` body the page has
    no hydrated state, so the adapter reports an honest Stale marker instead of
    guessing.
  - The browser adapter ranks first (220) for Douyin/Xiaohongshu/Kuaishou only
    while enabled; missing Playwright, an uncleared challenge or the off switch
    all degrade through `AdapterStale` into the unchanged f2 -> SSR ->
    gallery-dl -> yt-dlp chain.
- **Stealth fingerprint normalisation + residential-network evidence:**
  - The browser context now always presents a desktop Chrome User-Agent (never
    `HeadlessChrome`) and injects an init script masking `navigator.webdriver`,
    the empty plugin list and language signals. The UA is shared with the SSR
    and browser CDN clients from one constant in `browser_session`.
  - On a clean China Telecom residential IP, headed mode plus this stealth layer
    let the anonymous page hydrate real Xiaohongshu notes (an image set and a
    video, titles and media counts verified) and reach the Kuaishou
    recommendation feed with a real `did` cookie; the same context in headless
    was still redirected to login/`300012`. Xiaohongshu/Kuaishou therefore need
    `MEDIAVAULT_BROWSER_HEADLESS=false`; Douyin continues to work headless.
  - Dense automated visits tripped a temporary Xiaohongshu quarantine even with
    stealth (login/`300012` redirect that later also hit a previously passing
    headed run), confirming risk control is behavioural and time-based; the
    adapter keeps reporting Stale instead of retry-looping. This is fingerprint
    normalisation for anonymous reading, not login automation or account use.
- **Binary-free SSR adapter (always on, anonymous):**
  - New `ssr_state` extraction: a string-aware brace-matching scan plus tolerant
    JSON decode (bare `undefined` -> null) reads Xiaohongshu `__INITIAL_STATE__`
    and Kuaishou `__APOLLO_STATE__` straight from the server-rendered HTML, with
    no browser and no JavaScript execution.
  - New `SsrAdapter` (score 175) fetches the share document over plain httpx
    with a browser-like UA and reuses the existing pure note/photo parsers and
    the browser adapter's chunked streamer. It claims only Xiaohongshu/Kuaishou
    and ranks below f2 (200), above gallery-dl (150), until a residential-network
    success path is verified.
  - Evidence first: datacenter-IP probes showed Douyin SSR is an anti-bot shell,
    Xiaohongshu redirects to a login shell and Kuaishou ships only UI config
    (no `VisionVideoDetailPhoto`). Shell/risk documents therefore carry no detail,
    the extractor returns None and the adapter raises `AdapterStale` rather than
    guessing. Live runs confirm the f2 -> ssr -> CLI fallback degrades honestly.
  - New `MEDIAVAULT_SSR_TIMEOUT_MS` (default 20000) bounds the HTML fetch.
- **M3 local media intelligence, fully offline and key-free:**
  - Scene-cut-aware keyframes: `MEDIAVAULT_KEYFRAME_MODE=hybrid|scene` runs
    ffmpeg scene detection and merges cuts with uniform anchors inside the same
    frame budget; `interval` remains the unchanged default and every scene path
    degrades back to interval extraction on failure.
  - Dependency-free speaker turn labeling: `MEDIAVAULT_DIARIZATION=heuristic`
    tags transcript segments as `SPEAKER_A`/`SPEAKER_B` from inter-segment
    pauses (configurable `_DIARIZE_GAP_MS`), exposed via a narrow `Diarizer`
    protocol so an embedding-based backend can replace it later. These are
    conversational turns, not voiceprint-identified people.
  - New honest pipeline markers (`keyframe_mode`, `scene_cuts`,
    `diarization`) and `speaker_turns` in both evidence and summary payloads.
- Open-source release scaffolding: CI coverage gate (`--cov-fail-under=100`),
  CONTRIBUTING.md, SECURITY.md, package classifiers/keywords.

## 0.1.0

### Added
- stdio MCP server with seven tools: parse_url, download, analyze, summarize,
  get_job, list_jobs, list_library.
- Analyze-first design: the key-free `analyze` tool returns caption, local
  transcript and keyframe image blocks for the host agent's own model;
  `summarize` remains an optional server-side OpenAI-compatible fusion.
- Adapter fallback chain with scored registry: f2 (200) -> gallery-dl (150) ->
  yt-dlp (100); `AdapterStale` triggers transparent degradation instead of
  hard failure.
- Optional f2 CLI adapter for Douyin/Xiaohongshu/Kuaishou/TikTok/Weibo/
  Bilibili, and optional gallery-dl adapter for image-set posts; both
  auto-skip when the binary is absent.
- On-disk media library: atomic evidence.json/summary.json keyed by
  canonical_id, per-video cache reuse with `refresh=true`, and list_library.
- Central proxy/cookie policy (network.py): proxy scheme validation, Netscape
  cookie file checks, honest skip warnings, f2 YAML-cookie reminder.
- Local faster-whisper ASR (cpu/mps/cuda) via the `local-asr` extra for
  fully offline, key-free operation.

### Quality
- 228 offline tests at 100% statement and branch coverage, enforced in CI on
  Linux/macOS and Python 3.11/3.12.
