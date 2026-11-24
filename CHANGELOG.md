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
