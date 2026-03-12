# Speedtest Project — Improvement Plan

Staged improvement roadmap, ordered by priority.  
Work through each stage sequentially. Check off items as you go.

---

## ~~Stage 1 — Sensitive Data & Git Hygiene (Critical)~~ ✅ DONE

- [x] **1.1** Remove `config.json.bak` from the repository
- [x] **1.2** Add `.gitignore` entries for sensitive/generated files
      — `.gitignore` already covered most items; added `config.json` as the
      missing entry
- [ ] **1.3** ⚠️ Scrub `config.json` from git history (`git filter-repo`)
      — **Manual step:** run `git filter-repo --path config.json --invert-paths`
      then force-push. Do this when ready.
- [x] **1.4** Create/update `.env.example` documenting every environment variable
      — file already existed; added missing `AUTH_SALT` entry
- [ ] **1.5** ⚠️ Rotate any credentials that were exposed (SMTP password, email
      accounts) — **Manual step:** change passwords on your SMTP provider

**Why:** Personal information, account numbers, and SMTP details are committed in
plain text. Backup files should never be tracked.

---

## ~~Stage 2 — Auth & Session Security (High)~~ ✅ DONE

- [x] **2.1** Make `AUTH_SALT` required — fail at startup if the env var is missing
      — Removed `secrets.token_hex(16)` fallback; added validation in
      `validate_security_configuration()` that raises `RuntimeError` if unset
- [x] **2.2** Add a `Content-Security-Policy` response header in `web/app.py`
      — Already implemented via `add_security_headers` middleware (also sets
      `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
      `Permissions-Policy`)
- [x] **2.3** Add rate-limiting or lockout on failed login attempts
      — Already implemented: `_is_login_blocked()` / `_record_failed_login()`
      with configurable `LOGIN_MAX_ATTEMPTS`, `LOGIN_WINDOW_SECONDS`,
      `LOGIN_BLOCK_SECONDS` env vars

**Why:** Random AUTH_SALT on restart invalidates all sessions silently. Missing CSP
leaves the dashboard more vulnerable to XSS injection.

---

## ~~Stage 3 — Consolidate Log Parsing (High)~~ ✅ DONE

- [x] **3.1** Make `log_parser.parse_weekly_log_file()` the single source of truth
- [x] **3.2** Refactor `SpeedChart.py` to use `log_parser` instead of inline regex
      — Replaced ~45 lines of manual parsing with `parse_weekly_log_file()` call;
      removed unused `re` import
- [x] **3.3** Refactor `SendWeeklyReport.py` to use `log_parser` instead of its own
      parsing — `parse_log_file()` and `parse_log_for_table()` are now thin
      wrappers around `parse_weekly_log_file()`
- [x] **3.4** Remove the now-dead parsing code from both files
      — All inline regex/manual parsing replaced

**Why:** Three files independently parse the same log format. Bugs fixed in one
copy won't be fixed in the others.

---

## ~~Stage 4 — Configuration Consistency (Medium)~~ ✅ DONE

- [x] **4.1** Standardise all scripts to env-first, `config.json`-fallback pattern
      — Already consistent: `mail_settings.py`, `CheckSpeed.py`, `web/app.py`,
      and `scheduler_service.py` all use env-first for deployment-sensitive
      settings. Remaining scripts read paths/thresholds from config.json which
      is appropriate for those static values.
- [x] **4.2** Remove the unused `"database_file": "speedtest.db"` key from
      `config.json`
- [x] **4.3** Move `KEEP_WEEKS` and `KEEP_DAYS` from hardcoded values in
      `rotate_logs.py` into `config.json` / env vars — now reads
      `data_retention.keep_weeks` / `data_retention.keep_days` from config.json
      with `KEEP_WEEKS` / `KEEP_DAYS` env-var overrides
- [x] **4.4** Move `deploy.sh` defaults (`SERVER_USER`, `SERVER_HOST`) to env vars
      or prompt at runtime — reads `DEPLOY_USER`, `DEPLOY_HOST`, `DEPLOY_PATH`;
      prompts interactively if unset

**Why:** Inconsistent config loading makes the system harder to reason about and
deploy.

---

## ~~Stage 5 — Error Handling Hardening (Medium)~~ ✅ DONE

- [x] **5.1** Wrap log parsing in `SpeedChart.py` in try/except so a failure
      doesn't crash chart generation — added try/except around
      `parse_weekly_log_file()` call
- [x] **5.2** Add empty-DataFrame guard in `annual_report.py` before
      `sort_values('timestamp')` — added `if df.empty` check after
      `pd.DataFrame()` construction
- [x] **5.3** Validate that the `.env` file exists in `docker-entrypoint.sh` at
      startup — prints a warning if neither `/app/.env` nor `/data/.env` exists

**Why:** Edge cases in log files or missing config will hard-crash scripts that
otherwise work fine.

---

## ~~Stage 6 — Clean Up Dead / Legacy Code (Medium)~~ ✅ DONE

- [x] **6.1** Mark `credentials_manager.py` and `update_credentials.py` as legacy
      — Added DEPRECATED notice to both module docstrings pointing to .env
- [x] **6.2** `quote` import in `SendAlert.py` — Actually used on line 244 for
      ntfy topic URL encoding. No change needed; initial scan was incorrect.
- [x] **6.3** Remove `test-permission.txt` — Deleted (was empty)
- [x] **6.4** `__pycache__/` — Already in `.gitignore` and not tracked in git.
      No change needed.

**Why:** Dead code and leftover files add confusion and maintenance overhead.

---

## ~~Stage 7 — Documentation (Medium)~~ ✅ DONE

- [x] **7.1** `.env.example` — Already completed in Stage 1; added `AUTH_SALT`,
      `KEEP_WEEKS`, `KEEP_DAYS`, and `DEPLOY_*` vars in later stages
- [x] **7.2** Add a **Troubleshooting** section to `README.md` — Expanded with
      Docker/dashboard items: AUTH_SALT errors, session expiry, SMTP in Docker,
      login lockout
- [x] **7.3** Add a **Configuration Reference** section listing all `config.json`
      fields and their env-var equivalents — Added full table with 30+ settings
      showing `.env` var, `config.json` key, and defaults
- [x] **7.4** Document the expected log format(s) in `log_parser.py` module
      docstring — Added examples of both multi-line and legacy pipe formats

**Why:** New users (or future-you) shouldn't have to read every script to
understand what variables are needed.

---

## ~~Stage 8 — Testing (Medium)~~ ✅ DONE

- [x] **8.1** Create `tests/` directory and `conftest.py`
      — Added `tests/__init__.py`, `tests/conftest.py` with shared fixtures
      (`tmp_log_dir`, `sample_config`)
- [x] **8.2** Add unit tests for `log_parser` (both multi-line and pipe formats)
      — 19 tests in `tests/test_log_parser.py` covering `_extract_float`,
      `_parse_pipe_line`, `parse_weekly_log_file` (multi-line, legacy pipe,
      mixed format, edge cases), and `load_all_log_entries`
- [x] **8.3** Add unit tests for `mail_settings.load_mail_settings()`
      — 7 tests in `tests/test_mail_settings.py` covering env-only, config
      fallback, env-overrides-config, and all validation error paths
- [x] **8.4** Add unit tests for config loading / env-var fallback logic
      — 7 tests in `tests/test_config.py` covering the three-tier
      env → config.json → hardcoded-default retention pattern
- [x] **8.5** Add a basic smoke test for the FastAPI health endpoint
      — 3 tests in `tests/test_health.py`: status 200, response body shape,
      and no-auth-required check

**Why:** No tests exist today. Even a thin layer catches regressions in
parsing and configuration.

---

## ~~Stage 9 — Logging & Observability (Low)~~ ✅ DONE

- [x] **9.1** Create a shared `logger_setup.py` module using Python's `logging`
      — `get_logger(name)` returns a named logger that writes to stderr with
      auto-flush; honours `LOG_LEVEL` env var (DEBUG/INFO/WARNING/ERROR)
- [x] **9.2** Migrate `CheckSpeed.py`, `SendAlert.py`, `SendWeeklyReport.py` to
      use the shared logger
      — All `print()` calls replaced with `log.info/warning/error/exception`;
      removed manual `flush=True` and `sys.stdout.reconfigure` boilerplate
- [x] **9.3** Add log-level differentiation (INFO / WARNING / ERROR)
      — Each former `print()` mapped to the appropriate level: `info` for
      progress, `warning` for non-fatal issues, `error` for failures,
      `exception` for unexpected crashes with tracebacks
- [x] **9.4** Consider JSON-structured log output for easier parsing
      — Set `LOG_FORMAT=json` env var to emit one JSON object per line
      (`{ts, level, logger, message}`); default remains human-readable

**Why:** Custom `print()`/`write()` calls are scattered everywhere. A unified
logger makes debugging and monitoring much easier.

---

## ~~Stage 10 — Code Quality Polish (Low)~~ ✅ DONE

- [x] **10.1** Add type hints to `SpeedChart.py` and `annual_report.py`
      — Added `from __future__ import annotations`, typed all function
      signatures, key variables, and matplotlib objects (`Figure`, `Axes`)
- [x] **10.2** Run a linter (`ruff` or `flake8`) and fix warnings
      — Configured `ruff` in `pyproject.toml` (line-length 120, E/W/F/I
      rules); auto-fixed import sorting, unused imports, trailing whitespace
      across all files; manually fixed bare `except` → `except (ValueError,
      KeyError)` (3 sites in `SendWeeklyReport.py`, 1 in `health_check.py`),
      removed unused variables (`packet_loss_max`, `status_text`,
      `deleted_count`). Only E501 in inline HTML template strings remains.
- [x] **10.3** Add `py.typed` marker or basic `mypy` config
      — Added `py.typed` marker file; added `[tool.mypy]` section to
      `pyproject.toml` with gradual-adoption settings (`check_untyped_defs`,
      `warn_return_any`); core modules pass mypy cleanly
- [x] **10.4** Pin dependency versions in `requirements.txt` if not already
      — Already pinned with `==` for all 9 dependencies; no change needed

**Why:** Type hints and linting catch bugs at edit time instead of at runtime.

---

## Progress Tracker

| Stage | Description                  | Status                     |
| ----- | ---------------------------- | -------------------------- |
| 1     | Sensitive Data & Git Hygiene | ✅ Done (1.3 & 1.5 manual) |
| 2     | Auth & Session Security      | ✅ Done                    |
| 3     | Consolidate Log Parsing      | ✅ Done                    |
| 4     | Configuration Consistency    | ✅ Done                    |
| 5     | Error Handling Hardening     | ✅ Done                    |
| 6     | Clean Up Dead / Legacy Code  | ✅ Done                    |
| 7     | Documentation                | ✅ Done                    |
| 8     | Testing                      | ✅ Done                    |
| 9     | Logging & Observability      | ✅ Done                    |
| 10    | Code Quality Polish          | ✅ Done                    |
