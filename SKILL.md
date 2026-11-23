---
name: mediavault
description: Parse and download native, no-watermark media from pasted social-media links (YouTube, X, TikTok, Instagram, Bilibili, Douyin, Xiaohongshu, Kuaishou and 1800+ yt-dlp sites), and summarize post/video content. Use whenever the user pastes a share link or share text containing a media URL and asks to parse, download, save, extract original media, or summarize the content.
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
3. User wants a summary / 文案 / 要点 -> call summarize, then poll get_job
   until status is succeeded or failed; surface the structured summary.
4. Multiple links in one message are handled in a single call.

## Honesty rules

- Report no_watermark exactly as returned: "native" means the source stream
  itself has no burned-in watermark; "unknown" means unverified. Never claim
  watermarks were removed by cropping or inpainting.
- If the response is marked degraded, say which adapter failed and that the
  result came from a fallback.
- Private/deleted/region-locked content may fail; relay the adapter error
  verbatim instead of guessing.
- Downloads are for the user's own study of public content; remind about
  platform terms and copyright when relevant.

## Environment

Configured via environment or .env: MEDIAVAULT_HOME, MEDIAVAULT_PROXY,
MEDIAVAULT_COOKIES_BROWSER / MEDIAVAULT_COOKIES_FILE, and (summaries)
OPENAI_BASE_URL / OPENAI_API_KEY / MEDIAVAULT_LLM_MODEL.
