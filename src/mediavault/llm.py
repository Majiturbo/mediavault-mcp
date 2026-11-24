"""Thin OpenAI-compatible chat client.

Any provider exposing /chat/completions with a Bearer key works: OpenAI,
volcengine Ark, deepseek, local vLLM/Ollama with an OpenAI shim, etc.
Phase 2 uses it for transcript correction, caption summaries and (vision
capable model permitting) keyframe understanding.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from mediavault.config import Config
from mediavault.network import normalize_proxy


class LLMError(Exception):
    """Raised when the OpenAI-compatible endpoint is missing or errors."""


@dataclass
class ChatMessage:
    role: str
    content: str | list[dict]


class OpenAICompatibleClient:
    def __init__(
        self,
        config: Config,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._timeout = timeout
        # Tests inject an httpx.MockTransport; production leaves this None.
        self._transport = transport

    def _require_key(self) -> None:
        if not self._config.openai_api_key:
            raise LLMError(
                "OPENAI_API_KEY is not configured; set it in the environment or .env "
                "before requesting LLM-powered summarization"
            )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> str:
        self._require_key()
        url = self._config.openai_base_url.rstrip("/") + "/chat/completions"
        payload: dict = {
            "model": model or self._config.llm_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._config.openai_api_key}"}

        async with httpx.AsyncClient(
            timeout=self._timeout,
            proxy=normalize_proxy(self._config.proxy),
            transport=self._transport,
        ) as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code >= 400:
                raise LLMError(f"{response.status_code} {response.text[:500]}")
            data = response.json()
        return data["choices"][0]["message"]["content"]
