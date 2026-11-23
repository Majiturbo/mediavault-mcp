"""Orchestration facade: the only layer MCP tools talk to.

Flow: pasted text -> URL extraction -> registry fallback chain -> normalized
Resource -> download or async multimodal summarization job.
"""

from __future__ import annotations

from pathlib import Path

from mediavault.adapters import AdapterError, build_default_registry
from mediavault.asr import build_asr_backend
from mediavault.config import Config
from mediavault.jobs import JobManager
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
        llm = OpenAICompatibleClient(self.config)
        asr_backend = build_asr_backend(self.config)
        vision = VisionAnalyzer(llm, self.config) if self.config.openai_api_key else None
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

    async def summarize(self, text: str, with_media: bool = True) -> str:
        # Fail fast before downloading anything: the fusion LLM call is the
        # one hard dependency of summarization. A non-empty value is enough,
        # so local OpenAI-compatible servers can pass a placeholder key.
        if not self.config.openai_api_key:
            raise LLMError(
                "summarize needs an OpenAI-compatible LLM endpoint: set OPENAI_BASE_URL "
                "and OPENAI_API_KEY (a placeholder works for local Ollama/LM Studio). "
                "parse_url and download work without any key."
            )

        async def run() -> dict:
            urls = parse_input(text)
            if not urls:
                raise AdapterError("no http(s) URL found in the input")
            resource = await self.registry.probe(urls[0])

            video_path: str | None = None
            media_note: str | None = None
            if with_media and resource.content_type != "image_set":
                try:
                    chain = self.registry.chain_for(urls[0])
                    adapter = next(a for a in chain if a.name == resource.adapter_used)
                    # speed profile: summarization needs evidence, not master quality
                    files = await adapter.download(resource, "speed")
                    video_path = self._pick_video(files)
                    if video_path is None:
                        media_note = "download:no-video-file"
                except AdapterError as exc:
                    # Honest degradation: caption-only summary still has value.
                    media_note = f"download:{type(exc).__name__}"
            elif resource.content_type == "image_set":
                media_note = "skipped:image-set"

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
            return {"resource_id": resource.canonical_id, "summary": summary}

        return self.jobs.submit("summarize", run)

    def get_job(self, job_id: str) -> dict:
        return self.jobs.get(job_id).to_dict()

    def close(self) -> None:
        """Release the background job loop (tests and orderly shutdown)."""
        self.jobs.close()

    def list_jobs(self) -> list[dict]:
        return [j.to_dict() for j in self.jobs.list()]
