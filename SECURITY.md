# Security policy

## Reporting a vulnerability

Please do not open a public issue for security reports. Email the maintainers
with a description, reproduction steps, and affected version. We will confirm
receipt and coordinate a fix and disclosure timeline.

## Threat model and guarantees

- mediavault-mcp runs locally as a stdio MCP server; it exposes no network
  listener of its own.
- Proxies and cookies (`MEDIAVAULT_PROXY`, `MEDIAVAULT_COOKIES_FILE`,
  `MEDIAVAULT_COOKIES_BROWSER`) stay on the local machine. They are passed
  only to the user-selected backends (yt-dlp, gallery-dl, f2) and the
  user-configured OpenAI-compatible endpoint, and are never written to logs,
  cached evidence, or the on-disk media library.
- No third-party parser API keys are built in. The only API key the project
  reads is the user's own `OPENAI_API_KEY`, sent solely to their configured
  `OPENAI_BASE_URL`.
- Cache artifacts (evidence.json, summary.json) contain media metadata and
  transcripts, not credentials. Keep `MEDIAVAULT_HOME` on a trusted volume.

## Dependency posture

Optional capabilities are kept as optional dependencies or external CLIs:
faster-whisper via the `local-asr` extra, and f2/gallery-dl as auto-detected
binaries. A missing optional component degrades functionality, it never
executes downloaded code or fetches a replacement automatically.

## Supported versions

Security fixes target the latest minor release on the main branch.
