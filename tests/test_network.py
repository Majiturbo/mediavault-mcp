"""Unit tests for the central proxy/cookie policy."""

import pytest

from mediavault.config import Config
from mediavault.network import (
    NetworkConfigError,
    cookie_file_status,
    f2_network_opts,
    gallerydl_network_opts,
    normalize_proxy,
    ytdlp_network_opts,
)


def make_config(tmp_path, **env):
    payload = {"MEDIAVAULT_HOME": str(tmp_path / "vault")}
    payload.update(env)
    return Config.load(payload)


def write_cookies(tmp_path, name="cookies.txt", valid=True):
    path = tmp_path / name
    if valid:
        path.write_text(
            "# Netscape HTTP Cookie File\n"
            ".example.com\tTRUE\t/\tFALSE\t9999999999\tsid\tabc\n",
            encoding="utf-8",
        )
    else:
        path.write_text("# only comments\n\n", encoding="utf-8")
    return path


def test_normalize_proxy_variants():
    assert normalize_proxy(None) is None
    assert normalize_proxy("") is None
    assert normalize_proxy("   ") is None
    assert normalize_proxy("127.0.0.1:7890") == "http://127.0.0.1:7890"
    assert normalize_proxy("  https://h:8080  ") == "https://h:8080"
    assert normalize_proxy("socks5h://h:1080") == "socks5h://h:1080"


def test_normalize_proxy_rejects_bad_values():
    with pytest.raises(NetworkConfigError, match="whitespace"):
        normalize_proxy("http://h with space:1")
    with pytest.raises(NetworkConfigError, match="not supported"):
        normalize_proxy("ftp://h:1")
    with pytest.raises(NetworkConfigError, match="no host"):
        normalize_proxy("http://")


def test_cookie_file_status(tmp_path):
    assert cookie_file_status(None) == "not-configured"
    assert cookie_file_status("") == "not-configured"
    assert cookie_file_status(str(tmp_path / "missing.txt")) == "missing"
    assert cookie_file_status(str(write_cookies(tmp_path))) == "ok"
    assert cookie_file_status(str(write_cookies(tmp_path, "bad.txt", False))) == "invalid"


def test_ytdlp_opts_full_and_degraded(tmp_path):
    cookies = write_cookies(tmp_path)
    cfg = make_config(
        tmp_path,
        MEDIAVAULT_PROXY="127.0.0.1:7890",
        MEDIAVAULT_COOKIES_FILE=str(cookies),
        MEDIAVAULT_COOKIES_BROWSER="chrome",
    )
    opts, warnings = ytdlp_network_opts(cfg)
    assert opts["proxy"] == "http://127.0.0.1:7890"
    assert opts["cookiefile"] == str(cookies)
    assert "cookiesfrombrowser" not in opts  # file wins over browser
    assert warnings == []

    # missing file -> warning + browser fallback
    cfg2 = make_config(
        tmp_path,
        MEDIAVAULT_COOKIES_FILE=str(tmp_path / "gone.txt"),
        MEDIAVAULT_COOKIES_BROWSER="safari",
    )
    opts2, warnings2 = ytdlp_network_opts(cfg2)
    assert "cookiefile" not in opts2
    assert opts2["cookiesfrombrowser"] == ("safari",)
    assert any("missing" in w for w in warnings2)

    # no network config at all -> empty
    opts3, warnings3 = ytdlp_network_opts(make_config(tmp_path))
    assert opts3 == {} and warnings3 == []


def test_gallerydl_opts_browser_and_invalid(tmp_path):
    cfg = make_config(tmp_path, MEDIAVAULT_COOKIES_BROWSER="firefox")
    args, warnings = gallerydl_network_opts(cfg)
    assert args == ["--cookies-from-browser", "firefox"]
    assert warnings == []

    bad = write_cookies(tmp_path, "bad.txt", valid=False)
    cfg2 = make_config(tmp_path, MEDIAVAULT_COOKIES_FILE=str(bad))
    args2, warnings2 = gallerydl_network_opts(cfg2)
    assert "--cookies" not in args2
    assert any("invalid" in w for w in warnings2)


def test_f2_opts_proxy_only_with_cookie_warning(tmp_path):
    cfg = make_config(
        tmp_path,
        MEDIAVAULT_PROXY="socks5://h:1",
        MEDIAVAULT_COOKIES_BROWSER="chrome",
    )
    args, warnings = f2_network_opts(cfg)
    assert args == ["-P", "socks5://h:1"]
    assert any("YAML config" in w for w in warnings)

    args2, warnings2 = f2_network_opts(make_config(tmp_path))
    assert args2 == [] and warnings2 == []


def test_cookie_read_oserror_is_invalid(tmp_path, monkeypatch):
    path = write_cookies(tmp_path)
    original_read_text = path.read_text

    def boom(self, *args, **kwargs):
        if self == path:
            raise OSError("disk vanished")
        return original_read_text(*args, **kwargs)

    monkeypatch.setattr("mediavault.network.Path.read_text", boom)
    assert cookie_file_status(str(path)) == "invalid"


def test_builders_propagate_invalid_proxy(tmp_path):
    cfg = make_config(tmp_path, MEDIAVAULT_PROXY="ftp://h:1")
    for builder in (ytdlp_network_opts, gallerydl_network_opts, f2_network_opts):
        with pytest.raises(NetworkConfigError):
            builder(cfg)
