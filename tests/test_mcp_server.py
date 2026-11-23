"""End-to-end MCP tests: in-memory session and a real stdio subprocess."""

import asyncio
import json
import os
import sys

import pytest

from mediavault import server
from mediavault.adapters.base import AdapterError

EXPECTED_TOOLS = {"parse_url", "download", "summarize", "get_job", "list_jobs"}


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

    async def summarize(self, text: str, with_media: bool = True) -> str:
        return "job123abc"

    def get_job(self, job_id: str):
        if job_id == "missing":
            raise KeyError(job_id)
        return {"id": job_id, "kind": "summarize", "status": "succeeded"}

    def list_jobs(self):
        return [{"id": "job123abc", "kind": "summarize", "status": "queued"}]


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
