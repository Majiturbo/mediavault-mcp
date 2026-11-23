"""OpenAI-compatible client tests against an in-process mock HTTP transport."""

import asyncio
import json

import httpx
import pytest

from mediavault.config import Config
from mediavault.llm import ChatMessage, LLMError, OpenAICompatibleClient


def make_config(tmp_path, **env):
    payload = {
        "MEDIAVAULT_HOME": str(tmp_path / "vault"),
        "OPENAI_BASE_URL": "https://llm.example.test/v1",
        "OPENAI_API_KEY": "sk-test",
    }
    payload.update(env)
    return Config.load(payload)


def run(coro):
    return asyncio.run(coro)


def test_chat_posts_bearer_and_returns_content(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "hello back"}}],
        })

    client = OpenAICompatibleClient(make_config(tmp_path), transport=httpx.MockTransport(handler))
    answer = run(client.chat([ChatMessage(role="user", content="hi")]))

    assert answer == "hello back"
    assert seen["url"] == "https://llm.example.test/v1/chat/completions"
    assert seen["authorization"] == "Bearer sk-test"
    assert seen["body"]["model"] == "gpt-4o-mini"
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}]


def test_json_mode_sets_response_format(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = OpenAICompatibleClient(make_config(tmp_path), transport=httpx.MockTransport(handler))
    run(client.chat([ChatMessage(role="user", content="x")], json_mode=True, model="custom-model"))
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["model"] == "custom-model"


def test_missing_api_key_raises_before_request(tmp_path):
    config = make_config(tmp_path, OPENAI_API_KEY="")
    # Config falls through to None for empty string
    assert config.openai_api_key is None
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    client = OpenAICompatibleClient(config, transport=transport)
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        run(client.chat([ChatMessage(role="user", content="hi")]))


def test_http_error_raises_llm_error(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    client = OpenAICompatibleClient(make_config(tmp_path), transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="429"):
        run(client.chat([ChatMessage(role="user", content="hi")]))


def test_vision_content_passes_through(tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "seen"}}]})

    client = OpenAICompatibleClient(make_config(tmp_path), transport=httpx.MockTransport(handler))
    content = [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]
    run(client.chat([ChatMessage(role="user", content=content)]))
    assert seen["body"]["messages"][0]["content"][1]["type"] == "image_url"
