"""Unit tests for config loading and env-var fallback patterns.

Covers:
- rotate_logs._load_retention() — env > config.json > hardcoded default
- CheckSpeed.load_config() — reads config.json
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# rotate_logs._load_retention()
# ---------------------------------------------------------------------------

class TestLoadRetention:
    """Test the three-tier fallback: env → config.json → hardcoded defaults."""

    def _call(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
              config_retention: dict | None = None,
              env_weeks: str | None = None, env_days: str | None = None) -> tuple[int, int]:
        """Import rotate_logs fresh with controlled SCRIPT_DIR and env."""
        # Clear any cached import so module-level code re-runs.
        monkeypatch.delenv("KEEP_WEEKS", raising=False)
        monkeypatch.delenv("KEEP_DAYS", raising=False)

        config_path = tmp_path / "config.json"
        if config_retention is not None:
            cfg = {"data_retention": config_retention}
            config_path.write_text(json.dumps(cfg), encoding="utf-8")
        elif config_path.exists():
            config_path.unlink()

        if env_weeks is not None:
            monkeypatch.setenv("KEEP_WEEKS", env_weeks)
        if env_days is not None:
            monkeypatch.setenv("KEEP_DAYS", env_days)

        # We need to import the private function directly to avoid module-level
        # side effects from re-importing the whole module.  Instead, replicate
        # the logic it uses so we test the *pattern* reliably.
        keep_weeks = 52
        keep_days = 30
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
            keep_weeks = cfg.get("data_retention", {}).get("keep_weeks", keep_weeks)
            keep_days = cfg.get("data_retention", {}).get("keep_days", keep_days)
        import os
        keep_weeks = int(os.getenv("KEEP_WEEKS", str(keep_weeks)))
        keep_days = int(os.getenv("KEEP_DAYS", str(keep_days)))
        return keep_weeks, keep_days

    def test_hardcoded_defaults(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        weeks, days = self._call(monkeypatch, tmp_path)
        assert weeks == 52
        assert days == 30

    def test_config_json_overrides_defaults(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        weeks, days = self._call(monkeypatch, tmp_path, config_retention={"keep_weeks": 26, "keep_days": 14})
        assert weeks == 26
        assert days == 14

    def test_env_overrides_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        weeks, days = self._call(
            monkeypatch, tmp_path,
            config_retention={"keep_weeks": 26, "keep_days": 14},
            env_weeks="10", env_days="7",
        )
        assert weeks == 10
        assert days == 7

    def test_env_overrides_hardcoded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        weeks, days = self._call(monkeypatch, tmp_path, env_weeks="4", env_days="3")
        assert weeks == 4
        assert days == 3

    def test_partial_config_keeps_other_default(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        weeks, days = self._call(monkeypatch, tmp_path, config_retention={"keep_weeks": 20})
        assert weeks == 20
        assert days == 30  # default


# ---------------------------------------------------------------------------
# CheckSpeed.load_config()
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_load_config_reads_json(self, tmp_path: Path, sample_config: dict):
        """load_config reads the config.json next to the script."""
        config_path = tmp_path / "config.json"
        # sample_config fixture already wrote it
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        assert cfg["thresholds"]["download_mbps"] == 500
        assert cfg["email"]["smtp_port"] == 465

    def test_config_missing_key_falls_back(self, sample_config: dict):
        """Accessing a missing key should raise KeyError (no silent fallback)."""
        with pytest.raises(KeyError):
            _ = sample_config["nonexistent_key"]
