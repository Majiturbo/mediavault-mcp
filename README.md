# mediavault-mcp

Local **stdio MCP server** for the "paste a link, get the original media"
workflow, plus **agent-native video understanding**. Adapter-based so new
platforms are added without touching the MCP surface, with an explicit
fallback chain per platform and an honest no-watermark model.

The MCP server does not need an LLM API key: an `analyze` tool downloads the
clip, transcribes speech on-device with faster-whisper and returns the
caption, timestamped transcript and keyframe **images as MCP content blocks**
for the calling agent's own model to reason over. A server-side `summarize`
fusion call is kept as an optional convenience for deployments that configure
their own OpenAI-compatible endpoint.

- **M0 (this repo):** yt-dlp adapter (1800+ sites incl. YouTube, X, TikTok,
  Instagram, Bilibili), normalized metadata, downloads, and key-free video
  analysis: on-device transcript plus keyframe image blocks for the calling
  agent, with optional server-side fusion through an OpenAI-compatible
  endpoint.
- M1 (in progress): optional f2 CLI adapter now ranks ahead of yt-dlp for
  Douyin/Xiaohongshu/Kuaishou/TikTok/Weibo/Bilibili and transparently falls back
  when the binary is absent or a page shape drifts; self-built SSR adapters next.
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

Optional f2 CLI for first-class CN-platform extraction (Douyin, Xiaohongshu,
Kuaishou, TikTok, Weibo, Bilibili). If f2 is not on PATH the registry simply
uses yt-dlp, so it is safe to omit:

    pip install f2            # or: pipx install f2
    # override the binary name/path with MEDIAVAULT_F2_BIN if needed

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
| analyze | share text or URL, with_media=true (default) | **no key**: JSON evidence + keyframe image blocks for the agent's own model |
| summarize | share text or URL, with_media=true (default) | optional server-side fused summary job (needs an LLM endpoint); poll with get_job |
| get_job | job id | job polling (queued/running/succeeded/failed) |
| list_jobs | - | recent background jobs, newest first |

Output layout: ~/MediaVault/<platform>/<canonical_id>/ with resource.json
next to the media files. Summary jobs additionally write audio.wav and a
keyframes/ folder next to the downloaded clip.

## Two ways to understand a video

### analyze: agent-native, no API key (recommended)

`analyze` is deterministic and key-free. It probes the link, downloads the
speed-profile clip, and returns mixed MCP content blocks:

1. one **text block** with JSON evidence: title, author, caption, a
   timestamped transcript and the pipeline status;
2. one **image block per keyframe** (ffmpeg samples up to
   MEDIAVAULT_KEYFRAME_MAX downscaled JPEGs across the timeline), so a
   vision-capable agent sees the actual frames.

Speech is transcribed on-device by local faster-whisper when installed, or
by a configured hosted ASR endpoint; every stage is fault-tolerant and
reports itself honestly in `pipeline` (ok | off | not-configured | extracted
| skipped-* | empty | error). The calling agent then writes the summary with
its own model - the MCP server never makes a third-party model call. Set
with_media=false for caption/metadata only, with no download.

### summarize: optional server-side fusion

`summarize` runs the same evidence collection and then makes one
OpenAI-compatible chat call that fuses caption, transcript and (when a VLM
is configured) visual observations into the stable structured schema below.
It requires OPENAI_API_KEY (a placeholder is enough for a local Ollama/LM
Studio shim) and fails fast with an actionable message when unconfigured;
without a key, use `analyze` instead. The three evidence channels are:

1. Caption / title / description - always available.
2. Speech transcript - ffmpeg extracts 16 kHz mono WAV, then either the
   OpenAI-compatible /audio/transcriptions endpoint or local faster-whisper
   transcribes it with timestamps.
3. Visual facts - when a server VLM is configured, one observation per frame;
   otherwise the frames are still returned as image blocks by `analyze`.

Every media stage is fault-tolerant: a missing audio stream, a disabled
backend or a failed vision call is reported honestly instead of failing the
whole job.

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

The block above is the `summarize` output shape; `analyze` returns the same
evidence fields in its JSON text block plus the keyframe image blocks. Set
with_media=false for a fast, download-free caption-only response. When the
video cannot be downloaded, the run still succeeds with caption/metadata and
records the reason under pipeline.errors. Image sets skip the media stages
(skipped:image-set).

### Fully local, no commercial API key

parse_url, download and analyze never need a key and never call a
third-party model: with local faster-whisper installed, analyze keeps both
audio and frames on-device and hands the evidence to your agent. If you
also want the MCP server itself to fuse a summary (the `summarize` tool),
point its OpenAI-compatible endpoint at a local server (Ollama, LM Studio,
vLLM); the key just has to be a non-empty placeholder because local servers
do not validate it. Example .env for Ollama plus on-device faster-whisper:

    OPENAI_BASE_URL=http://localhost:11434/v1
    OPENAI_API_KEY=ollama
    MEDIAVAULT_LLM_MODEL=qwen2.5          # any local chat model
    MEDIAVAULT_VLM_MODEL=llava            # a local vision model for keyframes
    MEDIAVAULT_ASR_BACKEND=local          # faster-whisper, audio stays on-device
    MEDIAVAULT_WHISPER_MODEL=base
    MEDIAVAULT_WHISPER_DEVICE=mps         # cpu on CI/Linux, mps on Apple Silicon, cuda on NVIDIA

Without a configured endpoint, parse_url/download still work, while summarize
fails fast with an explicit message instead of downloading first.

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
- analyze never calls a model: local ASR keeps audio on-device and frames
  come back as image blocks. Only the optional summarize fusion, hosted ASR
  and server VLM use the OpenAI-compatible endpoint you configure.
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
      server.py              stdio MCP tools incl. analyze image blocks
      service.py             orchestration facade (probe -> download -> summarize)
      routing.py             share-text URL extraction + platform rules
      adapters/
        base.py              Adapter protocol, AdapterError/AdapterStale
        registry.py          scored fallback chains
        f2_adapter.py        optional f2 CLI, CN platforms (Stale->yt-dlp fallback)
        ytdlp_adapter.py     generic 1800+ site backend
      models.py              platform-neutral Resource/MediaItem
      jobs.py                thread-owned async job table
      media.py               ffmpeg/ffprobe wrappers (audio, keyframes)
      asr.py                 hosted OpenAI-shape and local faster-whisper ASR
      vision.py              keyframe VLM analysis
      llm.py                 OpenAI-compatible chat client
      summarize.py           key-free evidence collection + optional fusion
      config.py              env / .env configuration
    tests/                   183 offline tests incl. stdio, ffmpeg and f2 fallback
    SKILL.md                 thin Agent Skill wrapper for chat agents

## Legal

MIT licensed. Only process publicly available content, respect each
platform's terms of service and applicable copyright, and keep downloads for
personal study. This project performs no DRM circumvention.
