# mediavault-mcp

Local **stdio MCP server** for the "paste a link, get the original media"
workflow, plus a real **multimodal video summarization** pipeline.
Adapter-based so new platforms are added without touching the MCP surface,
with an explicit fallback chain per platform and an honest no-watermark
model.

- **M0 (this repo):** yt-dlp adapter (1800+ sites incl. YouTube, X, TikTok,
  Instagram, Bilibili), normalized metadata, downloads, and multimodal
  summaries that fuse platform captions, speech transcription and keyframe
  vision analysis through any OpenAI-compatible endpoint.
- M1: f2 adapter + self-built SSR adapters (Douyin, Xiaohongshu, Kuaishou)
  in front of yt-dlp, with stale-adapter fallback.
- M2: gallery-dl image sets, cookie/proxy hardening, media library cache.
- M3: richer local models (speaker diarization, denser scene detection).

## Install (uv recommended; needs Python 3.11+)

    git clone <this-repo> mediavault-mcp
    cd mediavault-mcp
    uv venv --python 3.11
    uv pip install -e ".[dev]"
    cp .env.example .env   # fill in when you need proxy/cookies/LLM

ffmpeg is required: HD stream merges, and the audio/frame extraction used by
the summarization pipeline. Install with "brew install ffmpeg" (macOS) or
your distro's ffmpeg package.

For fully offline, local speech-to-text instead of the hosted ASR endpoint:

    uv pip install -e ".[local-asr]"   # adds faster-whisper

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
({"count": n, "results": [...]}) so multi-link messages never lose data.

| Tool | Input | Effect |
|---|---|---|
| parse_url | share text or URL | normalized metadata, no bytes downloaded |
| download | share text or URL, quality=compat/quality/speed | native media under MEDIAVAULT_HOME |
| summarize | share text or URL, with_media=true (default) | async multimodal summary job; poll with get_job |
| get_job | job id | job polling (queued/running/succeeded/failed) |
| list_jobs | - | recent background jobs, newest first |

Output layout: ~/MediaVault/<platform>/<canonical_id>/ with resource.json
next to the media files. Summary jobs additionally write audio.wav and a
keyframes/ folder next to the downloaded clip.

## Video summarization pipeline

summarize runs three independent evidence channels and fuses them in one
LLM call. Every media stage is fault-tolerant: a missing audio stream, a
disabled backend or a failed vision call is reported honestly instead of
failing the whole job.

1. Caption / title / description - always available.
2. Speech transcript - ffmpeg extracts 16 kHz mono WAV, then either the
   OpenAI-compatible /audio/transcriptions endpoint or local faster-whisper
   transcribes it with timestamps.
3. Visual facts - ffmpeg samples up to MEDIAVAULT_KEYFRAME_MAX downscaled
   JPEGs spread across the timeline; a vision-capable model returns one
   observation per frame.

The returned summary keeps a stable schema:

    {
      "tldr": "...",
      "chapters": [{"start_ms": 0, "end_ms": 2000, "topic": "..."}],
      "transcript": "full transcription text",
      "visual_facts": [{"frame": "frame_001.jpg", "index": 1, "observation": "..."}],
      "entities": {"products": [], "locations": [], "people": []},
      "hooks": ["..."],
      "language": "zh",
      "pipeline": {
        "source": "multimodal",            # or transcript+caption / vision+caption / caption-only
        "transcript": "ok",                # ok | off | not-configured | skipped-* | empty | error
        "keyframes": "ok",                 # same vocabulary
        "errors": [],
        "canonical_id": "douyin:..."
      }
    }

Set with_media=false for a fast, download-free caption-only summary. When
the video cannot be downloaded, the job still succeeds in caption-only mode
and records the reason under pipeline.errors. Image sets skip the media
stages (skipped:image-set).

### Summarization configuration

| Variable | Default | Meaning |
|---|---|---|
| OPENAI_BASE_URL | https://api.openai.com/v1 | any OpenAI-shape endpoint |
| OPENAI_API_KEY | - | required for chat, hosted ASR and VLM |
| MEDIAVAULT_LLM_MODEL | gpt-4o-mini | fusion/chat model |
| MEDIAVAULT_VLM_MODEL | gpt-4o-mini | vision-capable model for keyframes |
| MEDIAVAULT_ASR_BACKEND | openai | openai (hosted) / local (faster-whisper) / off |
| MEDIAVAULT_ASR_MODEL | whisper-1 | model id for /audio/transcriptions |
| MEDIAVAULT_WHISPER_MODEL | base | faster-whisper size when backend=local |
| MEDIAVAULT_WHISPER_DEVICE | cpu | local ASR device: cpu / mps (Apple Silicon) / cuda |
| MEDIAVAULT_KEYFRAME_MAX | 8 | max sampled frames per video |
| MEDIAVAULT_KEYFRAME_WIDTH | 768 | downscale width, caps VLM token usage |

## Design notes

- Adapters declare platforms/capabilities and return platform-neutral
  Resource objects; the registry walks scored fallback chains and converts
  AdapterStale into automatic degradation instead of hard failure.
- No-watermark means "source stream carries no burned-in watermark" (native
  play_addr / source MP4). We never crop or blur, and never claim removal we
  did not perform; unverified streams stay marked "unknown".
- No third-party parser API keys are baked in. Cookies and proxies are local
  configuration and never appear in URLs or logs.
- LLM/ASR/VLM calls go only to the OpenAI-compatible endpoint you configure;
  local ASR keeps audio on-device.
- The job manager runs its own event loop on a daemon thread, so background
  jobs are independent of the short-lived event loop that submitted them.

## Development

    # lint (ruff rules: E, F, I, UP, B, SIM)
    .venv/bin/ruff check src tests

    # full suite, offline, with branch coverage
    .venv/bin/python -m pytest -q

The suite is fully offline (all platform/HTTP/model calls are mocked) and
sits at 100% branch coverage of src/mediavault. It includes a real
stdio-subprocess transport test and end-to-end tests that drive genuine
ffmpeg audio/frame extraction on a synthetic clip (skipped automatically if
ffmpeg is absent). CI installs ffmpeg and runs lint + tests on Ubuntu/macOS
and Python 3.11/3.12 (see .github/workflows/ci.yml); build a wheel with
"uv build".

## Project layout

    src/mediavault/
      server.py              stdio MCP tools (object envelopes)
      service.py             orchestration facade (probe -> download -> summarize)
      routing.py             share-text URL extraction + platform rules
      adapters/
        base.py              Adapter protocol, AdapterError/AdapterStale
        registry.py          scored fallback chains
        ytdlp_adapter.py     generic 1800+ site backend
      models.py              platform-neutral Resource/MediaItem
      jobs.py                thread-owned async job table
      media.py               ffmpeg/ffprobe wrappers (audio, keyframes)
      asr.py                 hosted OpenAI-shape and local faster-whisper ASR
      vision.py              keyframe VLM analysis
      llm.py                 OpenAI-compatible chat client
      summarize.py           three-channel fusion pipeline
      config.py              env / .env configuration
    tests/                   151 offline tests incl. stdio and ffmpeg E2E
    SKILL.md                 thin Agent Skill wrapper for chat agents

## Legal

MIT licensed. Only process publicly available content, respect each
platform's terms of service and applicable copyright, and keep downloads for
personal study. This project performs no DRM circumvention.
