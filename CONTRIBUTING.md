# Contributing

## Setup

```bash
uv sync --extra dev
uv run pre-commit install
```

## Tests

```bash
uv run pytest -m "not live"   # what CI runs — mocked, deterministic
uv run pytest -v              # everything, including the live verification suite (hits real upstream APIs)
```

Only `tests/test_verification_suite.py` carries `@pytest.mark.live` — it exercises five frozen reference fingerprints plus one negative test against the actual Zenon/IAA/Crossref/etc. backends. Skip it in CI to keep the pipeline deterministic; run it locally before releases.

## Lint and format

```bash
uv run ruff check .
uv run ruff format .
```

The `pre-commit` hook runs both on every commit (auto-fix where possible).

## Type checking

```bash
uv run mypy src/dao_paper_search_mcp
```

Configured with `ignore_missing_imports = true` — gradual rollout, not strict.

## Coverage

```bash
uv run pytest -m "not live" --cov=dao_paper_search_mcp --cov-report=term-missing
```

CI fails if coverage drops below 40 %.

## Release

1. Bump `version` in `pyproject.toml`, update `CHANGELOG.md`, commit
2. Tag: `git tag v0.7.5 && git push --tags`
3. Create a GitHub Release for the tag — `publish.yml` builds and pushes to PyPI automatically via Trusted Publishing (no token required)
