# SpeedPulse Publishing Improvements

Target release: `v1.1.9`

This file tracks the work needed to make SpeedPulse easier to publish, understand, and use for other people on GitHub.

## Current Status

- Core quality checks pass:
  - `ruff check .`
  - `pytest -q`
- Docker build works
- Dashboard container starts successfully
- `/ready` returns `ready`
- Release workflow now runs quality checks before creating a GitHub release
- Clean-clone startup with `./quickstart.sh` was validated locally
- Initial account registration works when no dashboard credentials are preconfigured
- Readiness returns `503 not_ready` when PostgreSQL is unavailable
- Runtime secrets and generated data are excluded from git

## Already Improved

- Docker build context was reduced significantly
- Docker image packaging was cleaned up
- Helper scripts were aligned with the current `data/` layout
- Release workflow was gated behind lint, type-checks, and tests
- Legacy setup confusion was reduced by switching setup scripts to Docker-first behavior

## Recommended Before Public Promotion

### 1. Documentation Polish

- [ ] Add 2-4 screenshots or GIFs of the dashboard to `README.md`
- [x] Add a short "Who this is for" section in `README.md`
- [x] Add a short "What it does not do" section to set expectations
- [x] Add a simple upgrade section for users moving from one version to another
- [x] Add a troubleshooting section for the most common Docker / SMTP / speedtest issues

### 2. Public Repo Readiness

- [ ] Review all examples and screenshots for personal information before publishing
- [x] Confirm `.env`, logs, backups, and local data are never committed
- [x] Add a `CONTRIBUTING.md` file if you want outside contributions
- [x] Add issue templates and a pull request template
- [x] Add a changelog file if releases will be maintained long-term

### 3. Release Quality

- [x] Review current local changes in `reporting.py` and `tests/test_reporting.py` before tagging
- [ ] Create a clean release commit for `v1.1.9`
- [ ] Tag only after `main` is confirmed clean
- [x] Verify the GitHub Release notes are readable and not too generic
- [ ] Confirm the published image tag is available and pullable from GHCR
  Blocked until `v1.1.9` is pushed and the new image is published.

### 4. User Experience

- [ ] Test first-run flow from a clean directory using only `compose.deploy.yml`
  Compose startup was fixed and validated up through `postgres` and `migrate`, but the current public GHCR image is older than this branch and still needs a retest after publishing `v1.1.9`.
- [x] Test first-run flow from a clean clone using `./quickstart.sh`
- [x] Verify account creation works with no preconfigured dashboard credentials
- [ ] Verify password reset flow with real SMTP credentials
- [ ] Verify backup and restore flow from a fresh environment

### 5. Operational Confidence

- [x] Run one full end-to-end test with `scheduler`, `dashboard`, `postgres`, and `migrate`
- [ ] Confirm log import works with sample historical files
- [ ] Confirm charts and report generation work after fresh startup
- [x] Verify health and readiness behavior during dependency failures
- [ ] Confirm non-root runtime behavior on a second machine or VPS

### 6. Nice-to-Have Improvements

- [x] Add badges for latest release and container registry package
- [x] Add architecture diagram image to complement the text diagram
- [x] Add example `.env` comments for common deployment setups
- [x] Add a sample demo configuration for easier evaluation
- [x] Add a small roadmap section for the next 2-3 planned releases

## Suggested Release Checklist For `v1.1.9`

- [x] `ruff check .`
- [x] `pytest -q`
- [x] `docker compose build`
- [x] `docker compose up -d`
- [x] Check `http://localhost:8000/ready`
- [x] Review `README.md`
- [x] Review release notes
- [ ] Create tag `v1.1.9`

## Publish Decision

Current recommendation: source release is publishable. The main remaining blockers are release execution, a real SMTP/password-reset test, backup/restore verification, and a `compose.deploy.yml` retest after the `v1.1.9` image is published to GHCR.
