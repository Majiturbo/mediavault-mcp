"""Orchestration facade: the only layer MCP tools talk to.

Flow: pasted text -> URL extraction -> registry fallback chain -> normalized
Resource -> download or async multimodal summarization job.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mediavault.adapters import AdapterError, build_default_registry
from mediavault.asr import build_asr_backend
from mediavault.config import Config
from mediavault.jobs import JobManager
from mediavault.library import MediaLibrary
from mediavault.llm import LLMError, OpenAICompatibleClient
from mediavault.models import DownloadResult
from mediavault.routing import parse_input
from mediavault.summarize import Summarizer
from mediavault.vision import VisionAnalyzer

_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}


class MediaVaultService:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config.load()
        self.registry = build_default_registry(self.config)
        self.jobs = JobManager()
        self.library = MediaLibrary(self.config)
        # LLM-backed fusion is optional: without a key we still build the
        # key-free evidence path (local ASR + ffmpeg frames) for analyze().
        llm = (
            OpenAICompatibleClient(self.config) if self.config.openai_api_key else None
        )
        asr_backend = build_asr_backend(self.config)
        vision = VisionAnalyzer(llm, self.config) if llm is not None else None
        self._summarizer = Summarizer(llm, asr_backend, vision, self.config)

    async def parse_text(self, text: str) -> list[dict]:
        urls = parse_input(text)
        if not urls:
            raise AdapterError("no http(s) URL found in the input (share text or bare URL)")
        resources = []
        for url in urls:
            resources.append((await self.registry.probe(url)).to_dict())
        return resources

    async def download(self, text: str, quality: str = "compat") -> list[dict]:
        if quality not in {"quality", "compat", "speed"}:
            raise ValueError("quality must be one of: quality, compat, speed")
        urls = parse_input(text)
        if not urls:
            raise AdapterError("no http(s) URL found in the input")
        results: list[dict] = []
        for url in urls:
            resource = await self.registry.probe(url)
            # Route download back through the adapter that produced the resource.
            chain = self.registry.chain_for(url)
            adapter = next(a for a in chain if a.name == resource.adapter_used)
            files = await adapter.download(resource, quality)
            result = DownloadResult(
                resource=resource, files=files, output_dir=str(self.config.home)
            )
            results.append(result.to_dict())
        return results

    @staticmethod
    def _pick_video(files: list[str]) -> str | None:
        for path in files:
            if Path(path).suffix.lower() in _VIDEO_SUFFIXES:
                return path
        return None

    async def _acquire_video(self, resource, url) -> tuple[str | None, str | None]:
        """Download the speed-profile clip for evidence collection.

        Returns (video_path, media_note); a failure degrades to no local file
        instead of raising, so caption/metadata evidence still comes back.
        """
        if resource.content_type == "image_set":
            return None, "skipped:image-set"
        try:
            chain = self.registry.chain_for(url)
            adapter = next(a for a in chain if a.name == resource.adapter_used)
            files = await adapter.download(resource, "speed")
            video_path = self._pick_video(files)
            if video_path is None:
                return None, "download:no-video-file"
            return video_path, None
        except AdapterError as exc:
            return None, f"download:{type(exc).__name__}"

    async def analyze(
        self, text: str, with_media: bool = True, refresh: bool = False
    ) -> dict:
        """Key-free evidence collection for the host agent's own LLM.

        Probes the link, optionally downloads the clip, and returns caption +
        local transcript + on-disk keyframe paths (the server attaches the
        frames as MCP image blocks). No server-side model call is made.

        Results are cached per canonical_id. When refresh is False and a
        reusable evidence.json exists (frame files still present for media
        requests), the cached payload is returned with cached=True instead of
        re-downloading or re-transcribing.
        """
        urls = parse_input(text)
        if not urls:
            raise AdapterError("no http(s) URL found in the input")
        primary = urls[0]
        resource = await self.registry.probe(primary)

        if not refresh:
            cached = self.library.reusable_evidence(
                resource.canonical_id, with_media=with_media
            )
            if cached is not None:
                return self._with_request_envelope(cached, urls)

        video_path: str | None = None
        media_note: str | None = None
        if with_media:
            video_path, media_note = await self._acquire_video(resource, primary)

        bundle = await self._summarizer.collect_evidence(resource, video_path)
        payload = bundle.to_dict()
        payload["analyzed_at"] = datetime.now(UTC).isoformat()
        payload["cached"] = False
        if media_note:
            payload["pipeline"]["errors"].append(media_note)
            pipeline = payload["pipeline"]
            if video_path is None and pipeline["transcript"] == "off":
                pipeline["transcript"] = "skipped-no-local-file"
            if video_path is None and pipeline["keyframes"] in {"off", "not-configured"}:
                pipeline["keyframes"] = "skipped-no-local-file"
        # Persist before adding the request-only envelope (which differs per call).
        self.library.save_evidence(resource.canonical_id, payload)
        return self._with_request_envelope(payload, urls)

    @staticmethod
    def _with_request_envelope(payload: dict, urls: list) -> dict:
        # analyze is single-resource (its image blocks belong to one clip);
        # make any extra links explicit so the agent calls again instead of
        # assuming they were processed.
        payload["analyzed_url"] = urls[0].raw_url
        payload["urls_detected"] = len(urls)
        payload["additional_urls"] = [u.raw_url for u in urls[1:]]
        return payload

    def list_library(self) -> dict:
        """Index cached evidence/summary artifacts under MEDIAVAULT_HOME."""
        entries = self.library.index()
        return {"count": len(entries), "results": entries}

    async def summarize(self, text: str, with_media: bool = True) -> str:
        # Fail fast before downloading anything: the fusion LLM call is the
        # one hard dependency of server-side summarization. A non-empty value
        # is enough, so local OpenAI-compatible servers pass a placeholder key.
        if not self.config.openai_api_key:
            raise LLMError(
                "summarize needs an OpenAI-compatible LLM endpoint: set OPENAI_BASE_URL "
                "and OPENAI_API_KEY (a placeholder works for local Ollama/LM Studio). "
                "Alternatively call analyze(), which needs no key and returns transcript "
                "plus keyframe images for this agent's own model to reason over. "
                "parse_url and download also work without any key."
            )

        async def run() -> dict:
            urls = parse_input(text)
            if not urls:
                raise AdapterError("no http(s) URL found in the input")
            resource = await self.registry.probe(urls[0])

            video_path: str | None = None
            media_note: str | None = None
            if with_media:
                video_path, media_note = await self._acquire_video(resource, urls[0])

            summary = await self._summarizer.summarize(resource, video_path=video_path)
            if media_note:
                # media_note is only set when no local video reached the
                # pipeline, so the media stages never ran: make generic "off"
                # markers explicit while preserving backend-specific ones.
                summary["pipeline"]["errors"].append(media_note)
                pipeline = summary["pipeline"]
                if pipeline["transcript"] == "off":
                    pipeline["transcript"] = "skipped-no-local-file"
                if pipeline["keyframes"] == "off":
                    pipeline["keyframes"] = "skipped-no-local-file"
            self.library.save_summary(resource.canonical_id, summary)
            return {"resource_id": resource.canonical_id, "summary": summary}

        return self.jobs.submit("summarize", run)

    def get_job(self, job_id: str) -> dict:
        return self.jobs.get(job_id).to_dict()

    def close(self) -> None:
        """Release the background job loop (tests and orderly shutdown)."""
        self.jobs.close()

    def list_jobs(self) -> list[dict]:
        return [j.to_dict() for j in self.jobs.list()]
