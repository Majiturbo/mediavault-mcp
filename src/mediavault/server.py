"""stdio MCP server surface.

Five tools, intentionally small:
  parse_url  - resolve pasted share text/URLs to normalized metadata (no bytes)
  download   - fetch native media into MEDIAVAULT_HOME
  summarize  - submit an async caption-based summary job (M3 adds audio/vision)
  get_job    - poll one job
  list_jobs  - list recent jobs

All collection-returning tools use explicit object envelopes because FastMCP
only serializes the first element of a bare list into the text content block,
which would silently drop results for multi-link messages.
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

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
