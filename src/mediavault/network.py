"""Central proxy/cookie policy shared by every adapter and HTTP client.

Hardening goals, all deterministic and offline-testable:

  * normalize_proxy - one place that validates proxy URLs, accepts bare
    host:port by assuming HTTP, and rejects malformed values with a clear
    NetworkConfigError instead of letting yt-dlp/httpx fail cryptically.
  * cookie_file_status - verify a Netscape cookies.txt exists and looks
    parseable. A missing or malformed file is skipped with a warning rather
    than handed to a CLI that would abort the whole extraction.
  * per-backend option builders - yt-dlp dict options, gallery-dl/f2 argv
    lists, each returning human-readable warnings so the service layer can
    surface degraded configuration honestly on the Resource.

f2 consumes cookies through its own YAML config (its --cookie expects a
cookie header string, not a Netscape file), so we only pass --proxy there and
warn instead of inventing an incompatible file argument.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from mediavault.config import Config

_ALLOWED_SCHEMES = ("http", "https", "socks4", "socks5", "socks5h")
_MIN_NETSCAPE_FIELDS = 7


class NetworkConfigError(ValueError):
    """Invalid proxy/cookie configuration (a subclass of ValueError)."""


def normalize_proxy(raw: str | None) -> str | None:
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = "http://" + candidate
    if any(ch.isspace() for ch in candidate):
        raise NetworkConfigError(f"proxy contains whitespace: {raw!r}")
    parts = urlsplit(candidate)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise NetworkConfigError(
            f"proxy scheme {parts.scheme!r} not supported; "
            f"use one of {', '.join(_ALLOWED_SCHEMES)}"
        )
    if not parts.hostname:
        raise NetworkConfigError(f"proxy has no host: {raw!r}")
    return candidate


def cookie_file_status(path: str | None) -> str:
    """Return not-configured | missing | invalid | ok for a Netscape file."""
    if not path:
        return "not-configured"
    cookie_path = Path(path)
    if not cookie_path.is_file():
        return "missing"
    try:
        lines = cookie_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "invalid"
    for line in lines:
        if not line or line.startswith("#"):
            continue
        if len(line.split("\t")) >= _MIN_NETSCAPE_FIELDS:
            return "ok"
    return "invalid"


def ytdlp_network_opts(config: Config) -> tuple[dict, list[str]]:
    """yt-dlp option dict plus warnings for skipped/misconfigured settings."""
    opts: dict = {}
    warnings: list[str] = []
    proxy = normalize_proxy(config.proxy)
    if proxy:
        opts["proxy"] = proxy
    status = cookie_file_status(config.cookies_file)
    if status == "ok":
        opts["cookiefile"] = config.cookies_file
    elif status in {"missing", "invalid"}:
        warnings.append(f"cookies_file {config.cookies_file!r} is {status}; ignoring it")
    if "cookiefile" not in opts and config.cookies_browser:
        opts["cookiesfrombrowser"] = (config.cookies_browser,)
    return opts, warnings


def gallerydl_network_opts(config: Config) -> tuple[list[str], list[str]]:
    """gallery-dl argv (--proxy/--cookies/--cookies-from-browser) + warnings."""
    args: list[str] = []
    warnings: list[str] = []
    proxy = normalize_proxy(config.proxy)
    if proxy:
        args += ["--proxy", proxy]
    status = cookie_file_status(config.cookies_file)
    if status == "ok":
        args += ["--cookies", config.cookies_file]
    elif status in {"missing", "invalid"}:
        warnings.append(f"cookies_file {config.cookies_file!r} is {status}; ignoring it")
    if "--cookies" not in args and config.cookies_browser:
        args += ["--cookies-from-browser", config.cookies_browser]
    return args, warnings


def f2_network_opts(config: Config) -> tuple[list[str], list[str]]:
    """f2 argv: proxy only; cookies live in f2's own YAML config."""
    args: list[str] = []
    warnings: list[str] = []
    proxy = normalize_proxy(config.proxy)
    if proxy:
        args += ["--proxy", proxy]
    if config.cookies_file or config.cookies_browser:
        warnings.append(
            "f2 reads cookies from its own YAML config, not MEDIAVAULT_COOKIES_*; "
            "configure them in your f2 config file"
        )
    return args, warnings
