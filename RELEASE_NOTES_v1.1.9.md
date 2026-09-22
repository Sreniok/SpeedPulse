# SpeedPulse `v1.1.9` Release Notes

## Summary

`v1.1.9` focuses on release readiness, Docker cleanup, and public GitHub usability.

This version does not change the overall product direction. It improves the packaging, helper scripts, and release process so the project is easier for others to clone, run, and evaluate.

## Highlights

- Docker build context and image packaging were cleaned up
- Release creation now runs quality checks first
- Local helper scripts now match the current Docker-first deployment model
- README and release documentation were improved for public users
- Lint and test gates pass for the release candidate

## Operational Improvements

- Smaller and cleaner Docker image build inputs
- Better alignment between docs, scripts, and actual runtime layout
- Cleaner first-run setup for users starting from a fresh clone
- Safer release workflow for tagged versions

## Validation

- `ruff check .`
- `pytest -q`
- `docker build -t speedpulse-optimized .`
- `docker compose build dashboard`
- `http://localhost:8000/ready`

## Upgrade Notes

- Existing users should continue using the `data/` directory as the runtime storage location.
- If you use local helper scripts, prefer the updated Docker-first `setup.sh` and `quickstart.sh`.
- Tagged releases now expect the quality gate to pass before the GitHub release is created.

## Known Limitations

- SMTP still requires user configuration before email notifications and reports will work
- Screenshot assets are not included in the repository yet
- Some roadmap items remain intentionally out of scope for this release

## Suggested GitHub Release Description

SpeedPulse `v1.1.9` improves publish readiness and operational consistency.

This release tightens the Docker image build, aligns helper scripts with the current Docker-first setup, adds release gating before GitHub releases, and improves the documentation for first-time users.
