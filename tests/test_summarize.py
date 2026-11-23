"""Summarizer tests with a fake LLM; no network and no real model."""


from mediavault.models import Author, Resource
from mediavault.summarize import Summarizer


class FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict] = []

    async def chat(self, messages, *, model=None, temperature=0.3, json_mode=False):
        self.calls.append({
            "messages": messages,
            "json_mode": json_mode,
            "model": model,
        })
        return self.response


def resource(**overrides):
    base = dict(
        canonical_id="douyin:1",
        platform="douyin",
        content_type="video",
        page_url="https://v.douyin.com/x/",
        title="标题",
        author=Author(name="博主"),
        caption="这是一条带百分号 100% 和大括号 {json} 的文案",
    )
    base.update(overrides)
    return Resource(**base)


async def _run(summarizer, res):
    return await summarizer.summarize(res)


def test_structured_json_summary_and_pipeline_markers():
    import asyncio

    fake = FakeLLM(
        '{"tldr": "要点", "chapters": [], "entities": {"products": []},'
        ' "hooks": ["开场"], "language": "zh"}'
    )
    result = asyncio.run(_run(Summarizer(fake), resource()))

    assert result["tldr"] == "要点"
    assert result["hooks"] == ["开场"]
    assert result["pipeline"] == {
        "source": "caption-only",
        "transcript": "skipped-m3",
        "keyframes": "skipped-m3",
        "canonical_id": "douyin:1",
    }
    assert fake.calls[0]["json_mode"] is True


def test_prompt_survives_percent_and_braces():
    import asyncio

    fake = FakeLLM("{}")
    # Must not raise despite '%' and braces in caption (token replacement, not formatting)
    result = asyncio.run(_run(Summarizer(fake), resource()))
    prompt = fake.calls[0]["messages"][0].content
    assert "100%" in prompt
    assert "{json}" in prompt
    assert "博主" in prompt
    assert result["pipeline"]["canonical_id"] == "douyin:1"


def test_non_json_response_is_wrapped_not_crashed():
    import asyncio

    fake = FakeLLM("模型自由发挥的纯文本总结")
    result = asyncio.run(_run(Summarizer(fake), resource()))
    assert result["tldr"] == "模型自由发挥的纯文本总结"
    assert result["chapters"] == []
    assert result["language"] == "unknown"


def test_long_caption_is_truncated():
    import asyncio

    fake = FakeLLM("{}")
    res = resource(caption="字" * 20000)
    asyncio.run(_run(Summarizer(fake), res))
    prompt = fake.calls[0]["messages"][0].content
    # 8000 chars of caption plus the fixed prompt scaffolding
    assert prompt.count("字") == 8000


def test_missing_caption_still_works():
    import asyncio

    fake = FakeLLM("{}")
    res = resource(caption=None)
    result = asyncio.run(_run(Summarizer(fake), res))
    assert result["pipeline"]["source"] == "caption-only"
