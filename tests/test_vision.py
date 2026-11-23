"""Vision analyzer tests with a fake LLM client; no network."""

import base64

from mediavault.config import Config
from mediavault.vision import VisionAnalyzer


class FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    async def chat(self, messages, *, model=None, temperature=0.3, json_mode=False):
        self.calls.append({"messages": messages, "model": model, "json_mode": json_mode})
        return self.response


def make_config(tmp_path, **env):
    payload = {
        "MEDIAVAULT_HOME": str(tmp_path / "vault"),
        "OPENAI_API_KEY": "sk-test",
        "MEDIAVAULT_VLM_MODEL": "qwen-vl-test",
    }
    payload.update(env)
    return Config.load(payload)


def make_frames(tmp_path, count=3):
    frames = []
    for i in range(1, count + 1):
        p = tmp_path / f"frame_{i:03d}.jpg"
        p.write_bytes(bytes([0xFF, 0xD8, i]))  # tiny fake jpeg bytes
        frames.append(p)
    return frames


def test_empty_frames_returns_empty(tmp_path):
    analyzer = VisionAnalyzer(FakeLLM("[]"), make_config(tmp_path))
    assert __import__("asyncio").run(analyzer.describe_frames([])) == []


def test_describe_frames_builds_data_urls_and_parses_array(tmp_path):
    fake = FakeLLM(
        '[{"index": 1, "observation": "片头标题"},'
        ' {"index": 2, "observation": "产品特写"},'
        ' {"index": 3, "observation": "结尾 CTA"}]'
    )
    analyzer = VisionAnalyzer(fake, make_config(tmp_path))
    frames = make_frames(tmp_path)
    facts = __import__("asyncio").run(analyzer.describe_frames(frames))

    assert [f.index for f in facts] == [1, 2, 3]
    assert facts[0].frame == "frame_001.jpg"
    assert facts[1].observation == "产品特写"

    call = fake.calls[0]
    assert call["model"] == "qwen-vl-test"
    assert call["json_mode"] is True
    content = call["messages"][0].content
    assert content[0]["type"] == "text"
    image_parts = [c for c in content if c["type"] == "image_url"]
    assert len(image_parts) == 3
    expected_b64 = base64.b64encode(frames[0].read_bytes()).decode("ascii")
    assert image_parts[0]["image_url"]["url"] == f"data:image/jpeg;base64,{expected_b64}"


def test_non_json_response_degrades_to_single_fact(tmp_path):
    fake = FakeLLM("模型直接输出的一段画面描述")
    analyzer = VisionAnalyzer(fake, make_config(tmp_path))
    facts = __import__("asyncio").run(analyzer.describe_frames(make_frames(tmp_path, 2)))
    assert len(facts) == 1
    assert facts[0].index == 1
    assert facts[0].frame == "frame_001.jpg"
    assert facts[0].observation == "模型直接输出的一段画面描述"


def test_valid_json_non_array_degrades_to_single_fact(tmp_path):
    fake = FakeLLM('{"tldr": "模型返回了对象而不是数组"}')
    analyzer = VisionAnalyzer(fake, make_config(tmp_path))
    facts = __import__("asyncio").run(analyzer.describe_frames(make_frames(tmp_path, 2)))
    assert len(facts) == 1
    assert facts[0].frame == "frame_001.jpg"
    assert "对象而不是数组" in facts[0].observation


def test_malformed_entries_skipped_and_index_clamped(tmp_path):
    fake = FakeLLM(
        '[{"index": 99, "observation": "越界索引取最后一帧"},'
        ' {"no_observation": true}, "garbage",'
        ' {"index": 1, "observation": "正常"}]'
    )
    analyzer = VisionAnalyzer(fake, make_config(tmp_path))
    facts = __import__("asyncio").run(analyzer.describe_frames(make_frames(tmp_path, 3)))
    assert len(facts) == 2
    assert facts[0].frame == "frame_003.jpg"  # clamped
    assert facts[1].observation == "正常"


def test_available_reflects_api_key(tmp_path):
    from mediavault.llm import OpenAICompatibleClient

    analyzer = VisionAnalyzer(OpenAICompatibleClient(make_config(tmp_path)), make_config(tmp_path))
    assert __import__("asyncio").run(_check(analyzer)) is True

    no_key_cfg = make_config(tmp_path, OPENAI_API_KEY="")
    no_key = VisionAnalyzer(OpenAICompatibleClient(no_key_cfg), no_key_cfg)
    assert __import__("asyncio").run(_check(no_key)) is False


async def _check(analyzer):
    return await analyzer.available()