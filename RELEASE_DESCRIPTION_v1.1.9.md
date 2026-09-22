# SpeedPulse v1.1.9

SpeedPulse `v1.1.9` focuses on publish readiness, Docker cleanup, and release quality.

This release improves the project for public GitHub use by tightening the Docker image build, aligning helper scripts with the current Docker-first setup, and enforcing quality checks before GitHub releases are created.

## Highlights

- Smaller and cleaner Docker build inputs
- Improved Docker image packaging
- Helper scripts updated to match the current `data/` runtime layout
- Release workflow now runs lint, type checks, and tests before creating a release
- README, changelog, and release documentation improved for first-time users

## Validation

- `ruff check .`
- `pytest -q`
- Docker build succeeds
- Dashboard readiness endpoint returns `ready`

## Notes

- SMTP still needs to be configured if you want email alerts and reports
- This release is mainly focused on release hardening and public usability, not major feature changes
