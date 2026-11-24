"""stdio MCP server surface.

Seven tools, intentionally small:
  parse_url    - resolve pasted share text/URLs to normalized metadata (no bytes)
  download     - fetch native media into MEDIAVAULT_HOME
  analyze      - key-free evidence: caption + local transcript + keyframe images
                 returned as content blocks for THIS agent's model to reason over;
                 reuses the on-disk cache unless refresh=true
  summarize    - optional server-side fusion job; needs a configured LLM endpoint
  get_job      - poll one job
  list_jobs    - list recent jobs
  list_library - index cached evidence/summary artifacts under MEDIAVAULT_HOME

All collection-returning tools use explicit object envelopes because FastMCP
only serializes the first element of a bare list into the text content block,
which would silently drop results for multi-link messages.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from mediavault.service import MediaVaultService

logging.basicConfig(level=logging.INFO)
mcp = FastMCP("mediavault")
_service: MediaVaultService | None = None


def service() -> MediaVaultService:
    # Lazy singleton: config/env is read on first tool call, not import time.
    global _service
    if _service is None:
        _service = MediaVaultService()
    return _service


@mcp.tool()
async def parse_url(text: str) -> dict:
    """Extract every URL from pasted share text and return normalized media
    metadata (platform, author, caption, direct streams, no-watermark status).
    Does not download any bytes."""
    results = await service().parse_text(text)
    return {"count": len(results), "results": results}


@mcp.tool()
async def download(text: str, quality: str = "compat") -> dict:
    """Download native media for every URL in the pasted text.

    quality: "compat" (default, <=1080p H.264 MP4), "quality" (highest source),
    or "speed" (smallest). Returns local file paths under MEDIAVAULT_HOME.
    """
    results = await service().download(text, quality=quality)
    return {"count": len(results), "results": results}


@mcp.tool()
async def analyze(text: str, with_media: bool = True, refresh: bool = False) -> list:
    """Collect video evidence WITHOUT any server-side API key, for this
    agent's own model to summarize. Returns one JSON text block (title,
    author, caption, timestamped transcript, pipeline status) followed by one
    image content block per extracted keyframe, so a vision-capable agent can
    see the frames directly. Local faster-whisper handles speech when
    configured; nothing is sent to a third-party model. Set with_media=false
    for caption/metadata only (no download). Results are cached per video:
    repeat calls return cached evidence (cached=true); set refresh=true to
    re-download and re-analyze. Use summarize instead only if you want the
    MCP server itself to produce the fused structured summary."""
    evidence = await service().analyze(
        text, with_media=with_media, refresh=refresh
    )
    blocks: list = [evidence]
    for frame_path in evidence.get("frame_paths", []):
        path = Path(frame_path)
        if path.exists():
            blocks.append(Image(path=path))
    return blocks


@mcp.tool()
async def list_library() -> dict:
    """List cached analyze/summary artifacts under MEDIAVAULT_HOME, newest
    discovery order, with canonical id, title and whether a fused summary is
    available. Re-run analyze with refresh=true to update an entry."""
    return service().list_library()


@mcp.tool()
async def summarize(text: str, with_media: bool = True) -> dict:
    """Submit an async multimodal summary job. Returns a job id; poll with
    get_job. With with_media=true (default) the video is downloaded at speed
    profile and analyzed through ASR transcript + keyframe VLM when those
    backends are configured; failures degrade honestly to caption-only.
    Set with_media=false for a metadata-only caption summary."""
    job_id = await service().summarize(text, with_media=with_media)
    return {"job_id": job_id, "poll_with": "get_job"}


@mcp.tool()
async def get_job(job_id: str) -> dict:
    """Return status and result of a summarize job (queued/running/succeeded/failed)."""
    try:
        return service().get_job(job_id)
    except KeyError:
        return {"error": f"unknown job_id: {job_id}"}


@mcp.tool()
async def list_jobs() -> dict:
    """List recent background jobs, newest first."""
    jobs = service().list_jobs()
    return {"count": len(jobs), "jobs": jobs}
