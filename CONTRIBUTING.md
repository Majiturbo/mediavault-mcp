# Contributing

Thanks for your interest in mediavault-mcp. This project keeps a strict
quality bar so the MCP server stays reliable when platform page shapes drift.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
Please open issues and pull requests using the templates under .github/.

## Development setup

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and ffmpeg on PATH
(the smoke tests skip themselves when ffmpeg/ffprobe are absent, but coverage
is only 100% with them installed).

    git clone <repo-url> mediavault-mcp
    cd mediavault-mcp
    uv venv --python 3.11
    uv pip install -e ".[dev]"
    # optional, for local speech-to-text tests against a real whisper model:
    uv pip install -e ".[local-asr]"

## The three commands that must pass

    .venv/bin/ruff check src tests
    .venv/bin/python -m pytest -q --cov-fail-under=100
    uv build

CI runs the same checks on Linux/macOS and Python 3.11/3.12. The suite is
fully offline: network access is mocked, and the optional f2/gallery-dl CLIs
are never actually invoked (their subprocess seams are monkeypatched).

## Coverage policy

100% statement and branch coverage is required, not aspirational. When you
add a branch, add the test that takes it. Defensive error paths (corrupt JSON,
missing binaries, invalid config) count as real behavior and get real tests.

## Adding or changing an adapter

1. Implement the `Adapter` ABC from `src/mediavault/adapters/base.py`.
2. Isolate every external assumption behind an overridable seam: binary lookup
   (`_which`), subprocess invocation (`_run`), and argv builders. Tests
   monkeypatch these instead of touching the network.
3. Raise `AdapterStale` for "the platform/CLI shape changed, try the next
   adapter" and `AdapterError` for terminal failures. Never return an empty
   Resource to mean failure.
4. Register the adapter in `build_default_registry` with a `match_score`
   that reflects its specificity (current chain: f2=200, gallery-dl=150,
   yt-dlp=100, unknown-host fallback=10).
5. Route all proxy/cookie options through `mediavault/network.py` so
   validation and honest skip warnings stay identical across backends.

## Honesty rules (non-negotiable product behavior)

- No-watermark is reported exactly as evidenced: `native`, `unknown`, or
  `none`. Never claim watermarks were removed by cropping or inpainting.
- Degradation is surfaced per stage in pipeline markers and Resource notes;
  one failed stage must not fail the whole pipeline.
- `analyze` never makes a model call. Only the optional `summarize` fusion,
  hosted ASR, and server VLM use the user-configured OpenAI-compatible
  endpoint. No third-party parser API keys are accepted or embedded.
- Cookies and proxies are local configuration: never log them, embed them in
  URLs, or send them anywhere except the user-configured backend.

## Commit and PR style

Use Conventional Commits prefixes already used in the history
(`feat:`, `fix:`, `docs:`, `test:`, `refactor:`), with the milestone in
parentheses where relevant (`feat(M2): ...`). Keep PRs focused: one adapter,
one policy change, or one doc update per review.
