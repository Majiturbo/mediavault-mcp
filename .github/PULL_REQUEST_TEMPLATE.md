## Summary

<!-- What does this PR change and why? Link any related issue, e.g. Closes #123 -->

## Type of change

- [ ] Bug fix
- [ ] New feature / adapter
- [ ] Refactor (no behavior change)
- [ ] Documentation
- [ ] Test / CI

## Checklist

- [ ] I ran the local gates and they pass:
      ruff check src tests
      python -m pytest -q --cov-fail-under=100
      uv build
- [ ] New behavior has tests; statement and branch coverage stays at 100%.
- [ ] External processes stay behind seams (_which / _run / argv builders); no live network in tests.
- [ ] Adapter failures use AdapterStale for fallback and AdapterError for terminal errors.
- [ ] Proxy/cookie changes go through mediavault/network.py; no credentials are logged or cached.
- [ ] No-watermark claims remain honest (native/unknown/none); analyze makes no model call.
- [ ] README / CHANGELOG / .env.example updated when behavior or configuration changes.

## Notes for reviewers

<!-- Anything subtle: argv contracts, platform quirks, degraded paths worth reading first. -->
