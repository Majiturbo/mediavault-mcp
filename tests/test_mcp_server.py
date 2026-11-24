"""End-to-end MCP tests: in-memory session and a real stdio subprocess."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from mediavault import server
from mediavault.adapters.base import AdapterError

EXPECTED_TOOLS = {
    "parse_url", "download", "analyze", "summarize", "get_job",
    "list_jobs", "list_library",
}


class FakeService:
    def __init__(self, *args, **kwargs):
        pass

    async def parse_text(self, text: str):
        if "://" not in text:
            raise AdapterError("no http(s) URL found in the input (share text or bare URL)")
        return [{"canonical_id": "douyin:1", "platform": "douyin", "adapter_used": "yt-dlp"}]

    async def download(self, text: str, quality: str = "compat"):
        return [{
            "files": ["/tmp/vault/douyin/x.mp4"],
            "output_dir": "/tmp/vault",
            "resource": {"canonical_id": "douyin:1"},
        }]

    async def analyze(self, text: str, with_media: bool = True, refresh: bool = False):
        self.last_refresh = refresh
        frame_dir = Path(tempfile.mkdtemp())
        real_frame = frame_dir / "frame_001.jpg"
        real_frame.write_bytes(bytes([255, 216, 1, 2, 3]))
        return {
            "canonical_id": "douyin:1",
            "title": "测试",
            "transcript": "完整 转写",
            "transcript_segments": [{"start_ms": 0, "end_ms": 1500, "text": "完整"}],
            "frames_extracted": 2,
            # Second path intentionally missing: the server must skip it.
            "frame_paths": [str(real_frame), str(frame_dir / "gone.jpg")],
            "visual_facts": [],
            "pipeline": {"source": "transcript+caption", "transcript": "ok",
                         "keyframes": "extracted", "errors": []},
        }

    async def summarize(self, text: str, with_media: bool = True) -> str:
        return "job123abc"

    def get_job(self, job_id: str):
        if job_id == "missing":
            raise KeyError(job_id)
        return {"id": job_id, "kind": "summarize", "status": "succeeded"}

    def list_jobs(self):
        return [{"id": "job123abc", "kind": "summarize", "status": "queued"}]

    def list_library(self):
        return {"count": 1, "results": [{"canonical_id": "douyin:1", "title": "缓存"}]}


@pytest.fixture
def fake_service(monkeypatch):
    monkeypatch.setattr(server, "MediaVaultService", FakeService)
    server._service = None
    yield
    server._service = None


def content_json(result):
    return json.loads(result.content[0].text)


async def _in_memory_scenarios():
    from mcp.shared.memory import create_connected_server_and_client_session as memory_session

    async with memory_session(server.mcp._mcp_server) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools.tools} == EXPECTED_TOOLS
        schema = {t.name: t.inputSchema for t in tools.tools}
        assert set(schema["download"]["properties"]) == {"text", "quality"}
        assert "text" in schema["parse_url"]["properties"]

        # parse_url success (object envelope, safe for multi-link messages)
        result = await client.call_tool("parse_url", {"text": "看看 https://v.douyin.com/x/"})
        assert result.isError is False
        parsed = content_json(result)
        assert parsed["count"] == 1
        assert parsed["results"][0]["canonical_id"] == "douyin:1"

        # parse_url error path is serialized, not raised over the wire
        result = await client.call_tool("parse_url", {"text": "no link at all"})
        assert result.isError is True
        assert "no http(s) URL" in result.content[0].text

        # download
        result = await client.call_tool(
            "download",
            {"text": "https://v.douyin.com/x/", "quality": "speed"},
        )
        assert result.isError is False
        assert content_json(result)["results"][0]["files"][0].endswith(".mp4")

        # analyze returns mixed blocks: JSON evidence text + keyframe images,
        # and needs no server-side API key. Missing frame files are skipped.
        result = await client.call_tool("analyze", {"text": "https://v.douyin.com/x/"})
        assert result.isError is False
        assert result.content[0].type == "text"
        evidence = json.loads(result.content[0].text)
        assert evidence["transcript"] == "完整 转写"
        assert len(result.content) == 2  # one text block + one existing image
        image_block = result.content[1]
        assert image_block.type == "image"
        assert image_block.mimeType == "image/jpeg"
        assert image_block.data
        assert "with_media" in schema["analyze"]["properties"]
        assert "refresh" in schema["analyze"]["properties"]

        # refresh flag is forwarded to the service
        await client.call_tool(
            "analyze",
            {"text": "https://v.douyin.com/x/", "refresh": True},
        )
        assert server._service.last_refresh is True

        # list_library exposes the on-disk cache index
        result = await client.call_tool("list_library", {})
        library = content_json(result)
        assert library["count"] == 1
        assert library["results"][0]["canonical_id"] == "douyin:1"

        # summarize returns a job handle and exposes the with_media switch
        result = await client.call_tool("summarize", {"text": "https://v.douyin.com/x/"})
        assert content_json(result) == {"job_id": "job123abc", "poll_with": "get_job"}
        assert "with_media" in schema["summarize"]["properties"]
        result = await client.call_tool(
            "summarize",
            {"text": "https://v.douyin.com/x/", "with_media": False},
        )
        assert content_json(result)["job_id"] == "job123abc"

        # get_job found / missing handled inside the tool
        result = await client.call_tool("get_job", {"job_id": "job123abc"})
        assert content_json(result)["status"] == "succeeded"
        result = await client.call_tool("get_job", {"job_id": "missing"})
        assert "error" in content_json(result)

        # list_jobs
        result = await client.call_tool("list_jobs", {})
        payload = content_json(result)
        assert payload["count"] == 1
        assert payload["jobs"][0]["id"] == "job123abc"


def test_mcp_tools_over_in_memory_transport(fake_service):
    asyncio.run(_in_memory_scenarios())


async def _stdio_body(tmp_path: str):
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mediavault"],
        env={**os.environ, "MEDIAVAULT_HOME": tmp_path},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert init.serverInfo.name == "mediavault"

        tools = await session.list_tools()
        assert {t.name for t in tools.tools} == EXPECTED_TOOLS

        # offline-safe error path proves the real process handles a call
        result = await session.call_tool("parse_url", {"text": "纯文本没有链接"})
        assert result.isError is True
        assert "no http(s) URL" in result.content[0].text


def test_mcp_stdio_subprocess_handshake_and_call(tmp_path):
    asyncio.run(_stdio_body(str(tmp_path)))
