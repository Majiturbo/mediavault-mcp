"""Configuration loaded from environment variables and .env files.

Precedence (highest first): real environment variables, ./.env,
MEDIAVAULT_HOME/.env, built-in defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOME = "~/MediaVault"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
DEFAULT_VLM_MODEL = "gpt-4o-mini"
DEFAULT_ASR_BACKEND = "openai"  # openai | local | off
DEFAULT_ASR_MODEL = "whisper-1"
DEFAULT_WHISPER_MODEL = "base"
DEFAULT_KEYFRAME_MAX = 8
DEFAULT_KEYFRAME_WIDTH = 768


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        values[key] = value.strip().strip('"').strip("'")
    return values


def _resolve(
    env: dict[str, str],
    dotenv: dict[str, str],
    key: str,
    default: str | None = None,
) -> str | None:
    if key in env and env[key] != "":
        return env[key]
    if key in dotenv and dotenv[key] != "":
        return dotenv[key]
    return default


def _resolve_int(
    env: dict[str, str],
    dotenv: dict[str, str],
    key: str,
    default: int,
) -> int:
    raw = _resolve(env, dotenv, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    home: Path
    proxy: str | None
    cookies_browser: str | None
    cookies_file: str | None
    # OpenAI-compatible chat completions endpoint
    openai_base_url: str
    openai_api_key: str | None
    llm_model: str
    vlm_model: str
    # Summarization pipeline
    asr_backend: str  # "openai" | "local" | "off"
    asr_model: str
    whisper_model: str
    keyframe_max: int
    keyframe_width: int

    @classmethod
    def load(cls, environ: dict[str, str] | None = None) -> Config:
        env = dict(os.environ if environ is None else environ)
        home_raw = env.get("MEDIAVAULT_HOME") or DEFAULT_HOME
        home = Path(home_raw).expanduser()

        dotenv: dict[str, str] = {}
        dotenv.update(_load_dotenv(home / ".env"))
        dotenv.update(_load_dotenv(Path.cwd() / ".env"))

        return cls(
            home=home,
            proxy=_resolve(env, dotenv, "MEDIAVAULT_PROXY"),
            cookies_browser=_resolve(env, dotenv, "MEDIAVAULT_COOKIES_BROWSER"),
            cookies_file=_resolve(env, dotenv, "MEDIAVAULT_COOKIES_FILE"),
            openai_base_url=(
                _resolve(env, dotenv, "OPENAI_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
            ),
            openai_api_key=_resolve(env, dotenv, "OPENAI_API_KEY"),
            llm_model=(
                _resolve(env, dotenv, "MEDIAVAULT_LLM_MODEL", DEFAULT_LLM_MODEL)
                or DEFAULT_LLM_MODEL
            ),
            vlm_model=(
                _resolve(env, dotenv, "MEDIAVAULT_VLM_MODEL", DEFAULT_VLM_MODEL)
                or DEFAULT_VLM_MODEL
            ),
            asr_backend=(
                _resolve(env, dotenv, "MEDIAVAULT_ASR_BACKEND", DEFAULT_ASR_BACKEND)
                or DEFAULT_ASR_BACKEND
            ).lower(),
            asr_model=(
                _resolve(env, dotenv, "MEDIAVAULT_ASR_MODEL", DEFAULT_ASR_MODEL)
                or DEFAULT_ASR_MODEL
            ),
            whisper_model=(
                _resolve(env, dotenv, "MEDIAVAULT_WHISPER_MODEL", DEFAULT_WHISPER_MODEL)
                or DEFAULT_WHISPER_MODEL
            ),
            keyframe_max=_resolve_int(env, dotenv, "MEDIAVAULT_KEYFRAME_MAX", DEFAULT_KEYFRAME_MAX),
            keyframe_width=_resolve_int(
                env, dotenv, "MEDIAVAULT_KEYFRAME_WIDTH", DEFAULT_KEYFRAME_WIDTH
            ),
        )

    def ensure_home(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        return self.home
