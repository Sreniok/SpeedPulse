# Contributing

Thanks for contributing to SpeedPulse.

## Scope

This project is a Docker-first self-hosted monitoring tool. Contributions are most useful when they improve:

- reliability
- clarity of setup and operations
- test coverage
- documentation
- security and release quality

## Before You Start

- Open an issue first for large changes
- Keep changes focused and easy to review
- Avoid mixing unrelated refactors with bug fixes
- Preserve the current Docker-first deployment model unless a change is intentionally expanding it

## Local Checks

Run these before opening a pull request:

```bash
./.venv/bin/ruff check .
./.venv/bin/pytest -q
docker compose build
```

If your change affects runtime behavior, also verify:

```bash
docker compose up -d
curl -fsS http://localhost:8000/ready
```

## Style

- Python version target is `3.12`
- Keep changes compatible with the existing project structure
- Prefer small, explicit changes over broad rewrites
- Update documentation when behavior or setup changes
- Add tests for bug fixes and behavior changes where practical

## Pull Requests

- Use a clear title
- Explain the user-visible impact
- Note any migration, config, or deployment changes
- Include verification steps you ran

## Security

- Do not commit secrets, `.env`, local runtime data, or backups
- Report sensitive security issues privately instead of opening a public issue
