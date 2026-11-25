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
DEFAULT_WHISPER_DEVICE = "cpu"  # cpu | mps (Apple Silicon) | cuda
DEFAULT_KEYFRAME_MAX = 8
DEFAULT_KEYFRAME_WIDTH = 768
DEFAULT_KEYFRAME_MODE = "interval"  # interval | hybrid | scene
DEFAULT_SCENE_THRESHOLD = 0.4  # ffmpeg scene-cut score, 0..1
DEFAULT_DIARIZATION = "off"  # off | heuristic
DEFAULT_DIARIZE_GAP_MS = 700  # pause length that starts a new speaker turn
DEFAULT_F2_BIN = "f2"  # optional f2 CLI; absence triggers yt-dlp fallback
DEFAULT_GALLERYDL_BIN = "gallery-dl"  # optional image-set CLI; absence -> fallback
# M4 local real-browser bootstrap for JS-VM challenge platforms (optional).
DEFAULT_BROWSER_BOOTSTRAP = "off"  # off | on
DEFAULT_BROWSER_ENGINE = "chromium"  # chromium | firefox | webkit
DEFAULT_BROWSER_HEADLESS = True
DEFAULT_BROWSER_TIMEOUT_MS = 30000
# Self-built server-side-render fetch (no browser); bounded HTTP read timeout.
DEFAULT_SSR_TIMEOUT_MS = 20000


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


def _resolve_float(
    env: dict[str, str],
    dotenv: dict[str, str],
    key: str,
    default: float,
) -> float:
    raw = _resolve(env, dotenv, key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _resolve_choice(
    env: dict[str, str],
    dotenv: dict[str, str],
    key: str,
    allowed: set[str],
    default: str,
) -> str:
    raw = (_resolve(env, dotenv, key, default) or default).lower()
    return raw if raw in allowed else default


def _resolve_bool(
    env: dict[str, str],
    dotenv: dict[str, str],
    key: str,
    default: bool,
) -> bool:
    raw = _resolve(env, dotenv, key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
    whisper_device: str
    keyframe_max: int
    keyframe_width: int
    # Denser scene-aware keyframe extraction and speaker diarization (M3).
    keyframe_mode: str  # interval | hybrid | scene
    scene_threshold: float
    diarization: str  # off | heuristic
    diarize_gap_ms: int
    # Optional external f2 CLI used by the CN-platform adapter.
    f2_bin: str
    # Optional external gallery-dl CLI for image sets.
    gallerydl_bin: str
    # M4 optional local browser that clears JS-VM challenges for anonymous cookies.
    browser_bootstrap: str  # off | on
    browser_engine: str  # chromium | firefox | webkit
    browser_headless: bool
    browser_timeout_ms: int
    # Timeout for the binary-free SSR HTML fetch used by the SSR adapter.
    ssr_timeout_ms: int

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
            whisper_device=(
                _resolve(env, dotenv, "MEDIAVAULT_WHISPER_DEVICE", DEFAULT_WHISPER_DEVICE)
                or DEFAULT_WHISPER_DEVICE
            ).lower(),
            keyframe_max=_resolve_int(env, dotenv, "MEDIAVAULT_KEYFRAME_MAX", DEFAULT_KEYFRAME_MAX),
            keyframe_width=_resolve_int(
                env, dotenv, "MEDIAVAULT_KEYFRAME_WIDTH", DEFAULT_KEYFRAME_WIDTH
            ),
            keyframe_mode=_resolve_choice(
                env, dotenv, "MEDIAVAULT_KEYFRAME_MODE",
                {"interval", "hybrid", "scene"}, DEFAULT_KEYFRAME_MODE,
            ),
            scene_threshold=_resolve_float(
                env, dotenv, "MEDIAVAULT_SCENE_THRESHOLD", DEFAULT_SCENE_THRESHOLD
            ),
            diarization=_resolve_choice(
                env, dotenv, "MEDIAVAULT_DIARIZATION",
                {"off", "heuristic"}, DEFAULT_DIARIZATION,
            ),
            diarize_gap_ms=_resolve_int(
                env, dotenv, "MEDIAVAULT_DIARIZE_GAP_MS", DEFAULT_DIARIZE_GAP_MS
            ),
            f2_bin=(
                _resolve(env, dotenv, "MEDIAVAULT_F2_BIN", DEFAULT_F2_BIN)
                or DEFAULT_F2_BIN
            ),
            gallerydl_bin=(
                _resolve(env, dotenv, "MEDIAVAULT_GALLERYDL_BIN", DEFAULT_GALLERYDL_BIN)
                or DEFAULT_GALLERYDL_BIN
            ),
            browser_bootstrap=_resolve_choice(
                env, dotenv, "MEDIAVAULT_BROWSER_BOOTSTRAP",
                {"off", "on"}, DEFAULT_BROWSER_BOOTSTRAP,
            ),
            browser_engine=_resolve_choice(
                env, dotenv, "MEDIAVAULT_BROWSER_ENGINE",
                {"chromium", "firefox", "webkit"}, DEFAULT_BROWSER_ENGINE,
            ),
            browser_headless=_resolve_bool(
                env, dotenv, "MEDIAVAULT_BROWSER_HEADLESS", DEFAULT_BROWSER_HEADLESS
            ),
            browser_timeout_ms=_resolve_int(
                env, dotenv, "MEDIAVAULT_BROWSER_TIMEOUT_MS", DEFAULT_BROWSER_TIMEOUT_MS
            ),
            ssr_timeout_ms=_resolve_int(
                env, dotenv, "MEDIAVAULT_SSR_TIMEOUT_MS", DEFAULT_SSR_TIMEOUT_MS
            ),
        )

    def ensure_home(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        return self.home
