"""Vision-language analysis of extracted keyframes.

Frames are sent as base64 data URLs in one chat request to the configured
OpenAI-compatible VLM. The model returns one JSON object per frame so visual
facts stay aligned with the source frames; malformed responses degrade to a
single unstructured observation instead of breaking the whole pipeline.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

from mediavault.config import Config
from mediavault.llm import ChatMessage, OpenAICompatibleClient

_VISION_PROMPT = """You are analyzing keyframes sampled from one social video.
For EVERY input frame (numbered 1..N in order), return a JSON array with one
object per frame: {"index": int, "observation": string}. Describe visible
on-screen text, products, scenes, people and actions concisely, in the video's
language. Only report what is visually present; never invent content.
Return ONLY the JSON array."""


@dataclass(frozen=True)
class VisualFact:
    frame: str
    index: int
    observation: str


class VisionAnalyzer:
    def __init__(self, llm: OpenAICompatibleClient, config: Config) -> None:
        self._llm = llm
        self._config = config

    @staticmethod
    def _encode_frame(path: Path) -> str:
        mime = "image/jpeg"
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _build_message(self, frames: list[Path]) -> ChatMessage:
        content: list[dict] = [{"type": "text", "text": _VISION_PROMPT}]
        for path in frames:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._encode_frame(path)},
                }
            )
        return ChatMessage(role="user", content=content)

    async def describe_frames(self, frames: list[Path]) -> list[VisualFact]:
        if not frames:
            return []
        raw = await self._llm.chat(
            [self._build_message(frames)],
            model=self._config.vlm_model,
            json_mode=True,
        )
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError("expected a JSON array of frame observations")
        except (json.JSONDecodeError, ValueError):
            # Endpoint ignored JSON mode or returned prose: keep it as one fact.
            return [VisualFact(frame=frames[0].name, index=1, observation=raw.strip()[:1000])]

        facts: list[VisualFact] = []
        for entry in parsed:
            if not isinstance(entry, dict) or "observation" not in entry:
                continue
            idx = int(entry.get("index", len(facts) + 1))
            frame_name = frames[idx - 1].name if 1 <= idx <= len(frames) else frames[-1].name
            facts.append(
                VisualFact(
                    frame=frame_name,
                    index=idx,
                    observation=str(entry["observation"]).strip(),
                )
            )
        return facts

    async def available(self) -> bool:
        """Vision stage requires a configured API key."""
        return bool(self._config.openai_api_key)
