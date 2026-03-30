# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project uses `vMAJOR.MINOR.PATCH` tags.

## [v1.2.0] - 2026-03-30

### Added

- Manual "Send weekly email" action in the dashboard top bar (next to manual speed test)
- New authenticated endpoint `POST /api/reports/weekly/send-now` for immediate weekly report delivery
- Focused tests for the manual weekly-report API flow

### Changed

- Weekly report selection now uses the last fully completed ISO week window
- Weekly report labels now include ISO week year context to avoid ambiguity around year boundaries

### Fixed

- Weekly report runs outside Monday no longer risk selecting an incorrect week bucket

## [v1.1.9] - 2026-03-20

### Added

- Publishing checklist in `PUBLISHING_IMPROVEMENTS.md`
- Release quality gate before GitHub release creation
- Docker-first helper flow for local setup and quick start
- Initial changelog and release notes draft for public publishing

### Changed

- Reduced Docker build context by excluding local runtime and development artifacts
- Reduced effective image size by replacing `COPY . .` plus recursive ownership changes with `COPY --chown`
- Updated `Makefile` to use the current `data/` runtime layout
- Updated deployment helper scripts to match the Docker-first project model
- Polished public-facing README content for first-time GitHub users

### Fixed

- Ruff failure in `reporting.py`
- Mismatch between documented runtime paths and helper scripts
- Release workflow creating GitHub releases without first running repo quality checks

### Notes

- `v1.1.9` is intended as a publish-ready cleanup and release-hardening update.
