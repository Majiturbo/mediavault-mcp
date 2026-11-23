# mediavault-mcp

Local **stdio MCP server** for the "paste a link, get the original media"
workflow. Adapter-based so new platforms are added without touching the MCP
surface, with an explicit fallback chain per platform and an honest
no-watermark model. A multimodal summarization pipeline is wired in and
expands in phases.

- **M0 (this repo):** yt-dlp adapter (1800+ sites incl. YouTube, X, TikTok,
  Instagram, Bilibili), normalized metadata, downloads, async caption-based
  summaries via any OpenAI-compatible endpoint.
- M1: f2 adapter + self-built SSR adapters (Douyin, Xiaohongshu, Kuaishou)
  in front of yt-dlp, with stale-adapter fallback.
- M2: gallery-dl image sets, cookie/proxy hardening, media library cache.
- M3: faster-whisper transcription + scene-aware keyframe VLM summary.

## Install (uv recommended; needs Python 3.11+)

    git clone <this-repo> mediavault-mcp
    cd mediavault-mcp
    uv venv --python 3.11
    uv pip install -e ".[dev]"
    cp .env.example .env   # fill in when you need proxy/cookies/LLM

ffmpeg is required for HD stream merges and (M3) audio extraction:
`brew install ffmpeg`.

## Run as an MCP server

    uv run python -m mediavault
    # or the installed console script
    mediavault

Client configuration (stdio), e.g. an MCP client JSON:

    {
      "mcpServers": {
        "mediavault": {
          "command": "uv",
          "args": ["--directory", "/abs/path/to/mediavault-mcp",
                   "run", "python", "-m", "mediavault"]
        }
      }
    }

## Tools

All collection-returning tools use an explicit object envelope
(`{"count": n, "results": [...]}`) so multi-link messages never lose data.

| Tool | Input | Effect |
|---|---|---|
| parse_url | share text or URL | normalized metadata, no bytes downloaded |
| download | share text or URL, quality=compat/quality/speed | native media under MEDIAVAULT_HOME |
| summarize | share text or URL | async job id; caption-based LLM summary today |
| get_job | job id | job polling (queued/running/succeeded/failed) |
| list_jobs | - | recent background jobs, newest first |

Output layout: `~/MediaVault/<platform>/<canonical_id>/` with
`resource.json` next to the media files.

## Design notes

- Adapters declare platforms/capabilities and return platform-neutral
  Resource objects; the registry walks scored fallback chains and converts
  AdapterStale into automatic degradation instead of hard failure.
- No-watermark means "source stream carries no burned-in watermark" (native
  play_addr / source MP4). We never crop or blur, and never claim removal we
  did not perform; unverified streams stay marked "unknown".
- No third-party parser API keys are baked in. Cookies and proxies are local
  configuration and never appear in URLs or logs.
- LLM calls go only to the OpenAI-compatible endpoint you configure.

## Development

    # lint (ruff rules: E, F, I, UP, B, SIM)
    .venv/bin/ruff check src tests

    # full suite, offline, with branch coverage
    .venv/bin/python -m pytest -q

The suite is fully offline (all platform/HTTP calls are mocked), includes a
real stdio-subprocess transport test, and currently sits at 100% branch
coverage of `src/mediavault`. CI runs lint + tests on Ubuntu/macOS and
Python 3.11/3.12 (see `.github/workflows/ci.yml`); build a wheel with
`uv build`.

## Project layout

    src/mediavault/
      server.py              stdio MCP tools (object envelopes)
      service.py             orchestration facade
      routing.py             share-text URL extraction + platform rules
      adapters/
        base.py              Adapter protocol, AdapterError/AdapterStale
        registry.py          scored fallback chains
        ytdlp_adapter.py     generic 1800+ site backend
      models.py              platform-neutral Resource/MediaItem
      jobs.py                in-process async job table
      llm.py                 OpenAI-compatible chat client
      summarize.py           summary pipeline (caption-only now, M3 hooks)
      config.py              env / .env configuration
    tests/                   97 offline tests incl. stdio subprocess E2E
    SKILL.md                 thin Agent Skill wrapper for chat agents

## Legal

MIT licensed. Only process publicly available content, respect each
platform's terms of service and applicable copyright, and keep downloads for
personal study. This project performs no DRM circumvention.
