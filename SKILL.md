---
name: mediavault
description: Parse and download native, no-watermark media from pasted social-media links (YouTube, X, TikTok, Instagram, Bilibili, Douyin, Xiaohongshu, Kuaishou and 1800+ yt-dlp sites), and gather key-free video evidence (transcript plus keyframe images) or produce fused summaries. Use whenever the user pastes a share link or share text containing a media URL and asks to parse, download, save, extract original media, or summarize the content.
---

# mediavault

A thin prompt wrapper around the local **mediavault stdio MCP server**. The
heavy logic (URL extraction, adapter fallback chains, downloads, async
summarization) lives in the MCP tools; this file only tells the agent when
and how to call them.

## When to use

Trigger on any message that contains an http(s) media link or platform share
blob (for example "8.41 复制打开抖音... https://v.douyin.com/..."), including
requests phrased as: 解析 / 下载 / 无水印 / 保存原图原片 / 总结 / 提取文案.

Do NOT use for: generic web pages with no media, DRM-protected content, or
requests to re-upload/redistribute downloaded content.

## Tool selection

1. User wants to see what a link contains -> call parse_url first, present
   title / author / platform / items / no_watermark flags, and ask before
   downloading large files.
2. User wants the file -> call download. Explain the three quality modes:
   - compat (default): <=1080p H.264 MP4, plays and edits everywhere
   - quality: highest source stream (may be HEVC/AV1, needs a modern player)
   - speed: smallest file, fastest
   - Image-set posts (content_type=image_set, e.g. Xiaohongshu/Instagram)
     return every source image; extraction walks the optional chain
     local browser (Douyin/Xiaohongshu/Kuaishou, when
     MEDIAVAULT_BROWSER_BOOTSTRAP=on) > f2 > SSR (binary-free XHS/Kuaishou
     hydrated-state fetch) > gallery-dl > yt-dlp and
     transparently falls back when a CLI/browser is absent or stale, surfaced
     via the degraded flag. The browser path clears the JS-VM challenge
     anonymously (no login) and reads the signed media in-page; its note reads
     "browser:anonymous in-page detail fetch". The SSR adapter note reads
     "ssr:anonymous server-rendered state (no browser, no account login)". A
     Xiaohongshu 300012 network-risk page or a Kuaishou {"result": 2} body has
     no hydrated state and degrades honestly rather than returning guessed
     content.
3. User wants a summary / 文案 / 要点:
   - Prefer analyze (no API key needed). It returns one JSON evidence block
     (title, author, caption, timestamped transcript, pipeline status)
     followed by one image content block per keyframe. Reason over these
     blocks with YOUR OWN model: read the transcript text and look at the
     frame images directly, then write the summary in the user's language.
     Local faster-whisper does speech on-device; nothing is sent to a
     third-party model. with_media=false gives caption/metadata only.
   - analyze is cached per video: a repeat call returns cached=true without
     re-downloading; pass refresh=true to force re-analysis. Use list_library
     to see cached entries before deciding whether to refresh.
   - Use summarize only when the deployment explicitly configured a
     server-side OpenAI-compatible endpoint and wants the MCP server itself
     to emit the fused structured summary; it returns a job id, so poll
     get_job until succeeded/failed. It raises a clear error without a key,
     in which case fall back to analyze.
   - Read pipeline markers honestly: source is multimodal | transcript+caption
     | vision+caption | frames+caption | caption-only; transcript/keyframes
     carry ok | off | not-configured | extracted | skipped-* | empty | error.
     Never claim audio/visual analysis the markers do not show, and when
     keyframes are "extracted" the evidence is in the image blocks, not text.
    - M3 evidence fields: pipeline.keyframe_mode (interval|hybrid|scene) and
      pipeline.scene_cuts report scene-aware extraction; pipeline.diarization
      is off | skipped-no-transcript | pause-gap-heuristic | error. When
      diarization ran, each transcript_segment carries a speaker label and the
      payload includes speaker_turns. These SPEAKER_A/SPEAKER_B tags are
      pause-based turns, not identified people - never name a real speaker.
4. Multiple links: parse_url and download handle every URL in one call.
   analyze is single-resource because its image blocks belong to one clip -
   call it once per link. Its payload reports urls_detected and
   additional_urls, so if additional_urls is non-empty, call analyze again
   for each remaining link rather than assuming they were covered.

## Honesty rules

- Report no_watermark exactly as returned: "native" means the source stream
  itself has no burned-in watermark; "unknown" means unverified. Never claim
  watermarks were removed by cropping or inpainting.
- If the response is marked degraded, say which adapter failed and that the
  result came from a fallback.
- Resource `notes` may carry network-policy warnings (a missing/invalid
  cookies.txt was skipped, or f2 needs cookies in its own YAML config); relay
  them instead of silently assuming the configured proxy/cookies were used.
- Private/deleted/region-locked content may fail; relay the adapter error
  verbatim instead of guessing.
- Downloads are for the user's own study of public content; remind about
  platform terms and copyright when relevant.

## Environment

Configured via environment or .env: MEDIAVAULT_HOME, MEDIAVAULT_PROXY,
MEDIAVAULT_COOKIES_BROWSER / MEDIAVAULT_COOKIES_FILE. parse_url, download and
analyze need NO API key. Only the optional server-side summarize fusion needs
OPENAI_BASE_URL / OPENAI_API_KEY (a placeholder works with local Ollama/LM
Studio) plus MEDIAVAULT_LLM_MODEL and MEDIAVAULT_VLM_MODEL. The pipeline is
tuned by MEDIAVAULT_ASR_BACKEND (openai|local|off), MEDIAVAULT_ASR_MODEL,
MEDIAVAULT_WHISPER_MODEL, MEDIAVAULT_WHISPER_DEVICE (cpu|mps|cuda),
MEDIAVAULT_KEYFRAME_MAX and MEDIAVAULT_KEYFRAME_WIDTH. Scene-aware frames use
MEDIAVAULT_KEYFRAME_MODE (interval|hybrid|scene, default interval) and
MEDIAVAULT_SCENE_THRESHOLD (0..1). Speaker turn labeling uses
MEDIAVAULT_DIARIZATION (off|heuristic) with MEDIAVAULT_DIARIZE_GAP_MS; the
heuristic is key-free and emits SPEAKER_A/SPEAKER_B turn tags (not identified
people), surfaced as speaker_turns and per-segment speaker fields. ffmpeg must
be on PATH for audio/frame/scene extraction; on-device ASR additionally needs
uv pip install -e ".[local-asr]". The optional f2 and gallery-dl CLIs are
auto-detected on PATH (override with MEDIAVAULT_F2_BIN /
MEDIAVAULT_GALLERYDL_BIN); neither is required, and both honor the proxy /
cookies settings when present. The optional browser path needs
uv pip install -e ".[browser]" plus `playwright install chromium` and is off by
default; MEDIAVAULT_BROWSER_ENGINE/HEADLESS/TIMEOUT_MS tune it, and a missing
Playwright or uncleared challenge degrades to the CLI chain via AdapterStale.
