"""Unit coverage for the speedtest runner."""

from __future__ import annotations

import json
import subprocess

import CheckSpeed
import health_check


def test_build_speedtest_command_enables_progress_for_ookla():
    cmd = CheckSpeed.build_speedtest_command(
        "/usr/bin/speedtest",
        "ookla",
        server_id="71403",
        live_progress=True,
    )

    assert cmd == [
        "/usr/bin/speedtest",
        "--accept-license",
        "--accept-gdpr",
        "--format=json",
        "--progress=yes",
        "--server-id",
        "71403",
    ]


def test_build_speedtest_command_keeps_scheduled_ookla_runs_compact():
    cmd = CheckSpeed.build_speedtest_command("/usr/bin/speedtest", "ookla", server_id="71403")

    assert "--progress=yes" not in cmd


def test_ookla_progress_logs_live_metrics(monkeypatch):
    messages = []

    def capture(message, *args):
        messages.append(message % args if args else message)

    monkeypatch.setattr(CheckSpeed.log, "info", capture)
    progress_state = {"ping": -20, "download": -5, "upload": -5}

    CheckSpeed._maybe_log_ookla_progress(  # noqa: SLF001
        {"type": "ping", "ping": {"latency": 6.42, "progress": 0.4}},
        progress_state,
    )
    CheckSpeed._maybe_log_ookla_progress(  # noqa: SLF001
        {"type": "download", "download": {"bandwidth": 12_500_000, "progress": 0.31}},
        progress_state,
    )
    CheckSpeed._maybe_log_ookla_progress(  # noqa: SLF001
        {"type": "upload", "upload": {"bandwidth": 4_500_000, "progress": 0.24}},
        progress_state,
    )

    assert messages == [
        "Idle Latency: 6.42 ms (40%)",
        "Download: 100.00 Mbps (31%)",
        "Upload: 36.00 Mbps (24%)",
    ]


def test_fallback_server_on_final_retry_and_error_classification(monkeypatch, tmp_path):
    error_log_path = tmp_path / "errors.log"
    config = {
        "paths": {
            "speedtest_exe": "speedtest",
            "error_log": str(error_log_path),
            "log_directory": str(tmp_path / "Log"),
        },
        "speedtest": {
            "server_id": "71403",
            "max_retries": 3,
            "retry_delay_seconds": 0,
            "timeout_seconds": 10,
        },
    }

    commands_executed = []
    log_info_messages = []
    log_warn_messages = []

    def mock_info(msg, *args):
        log_info_messages.append(msg % args if args else msg)

    def mock_warning(msg, *args):
        log_warn_messages.append(msg % args if args else msg)

    monkeypatch.setattr(CheckSpeed.log, "info", mock_info)
    monkeypatch.setattr(CheckSpeed.log, "warning", mock_warning)
    monkeypatch.setattr(CheckSpeed, "resolve_speedtest_executable", lambda _cfg: "/usr/bin/speedtest")
    monkeypatch.setattr(CheckSpeed, "detect_speedtest_provider", lambda _exe: "ookla")
    monkeypatch.setattr(CheckSpeed.time, "sleep", lambda _s: None)

    ookla_success_payload = {
        "type": "result",
        "download": {"bandwidth": 12500000},
        "upload": {"bandwidth": 6250000},
        "ping": {"latency": 15.2, "jitter": 2.1},
        "packetLoss": 0.0,
        "isp": "Sky Broadband",
        "interface": {"externalIp": "1.2.3.4"},
        "server": {
            "id": 4604,
            "name": "Blacknight",
            "location": "Dublin",
            "country": "Ireland",
        },
        "result": {"url": "https://www.speedtest.net/result/c/12345"},
    }

    def mock_run(cmd, capture_output=True, text=True, timeout=10):
        commands_executed.append(list(cmd))
        attempt_num = len(commands_executed)
        if attempt_num < 3:
            return subprocess.CompletedProcess(
                cmd,
                returncode=2,
                stdout="",
                stderr="Configuration - No servers defined (NoServersException)",
            )
        return subprocess.CompletedProcess(
            cmd,
            returncode=0,
            stdout=json.dumps(ookla_success_payload),
            stderr="",
        )

    monkeypatch.setattr(CheckSpeed.subprocess, "run", mock_run)

    result = CheckSpeed.run_speedtest_with_retry(config)

    # 1. Attempt 1 uses preferred server
    assert "--server-id" in commands_executed[0]
    assert "71403" in commands_executed[0]

    # 2. Attempt 2 uses preferred server
    assert "--server-id" in commands_executed[1]
    assert "71403" in commands_executed[1]

    # 3. Final attempt (3/3) omits --server-id
    assert "--server-id" not in commands_executed[2]
    assert "71403" not in commands_executed[2]

    # 4. Successful fallback returns actual Ookla-selected server
    assert result is not None
    assert result["server_id"] == "4604"
    assert result["server_name"] == "Blacknight"

    # 5. Preferred server in config is unchanged
    assert config["speedtest"]["server_id"] == "71403"

    # 6. Intermediate failures don't create counted ERROR entries
    assert not error_log_path.exists()
    assert health_check.check_error_log(config)["recent_errors"] == 0

    # Verify expected logging flow
    assert any("Selected preferred server #71403" in m for m in log_info_messages)
    assert any("Running Ookla CLI via speedtest, server_id=71403 (attempt 1/3)" in m for m in log_info_messages)
    assert any("Running Ookla CLI via speedtest, server_id=71403 (attempt 2/3)" in m for m in log_info_messages)
    assert any(
        "Preferred server #71403 failed on previous attempts; final retry will use automatic server selection" in m
        for m in log_info_messages
    )
    assert any("Running Ookla CLI via speedtest, automatic server selection (attempt 3/3)" in m for m in log_info_messages)
    assert any("Fallback server selected by Ookla: Blacknight – Dublin (id: 4604)" in m for m in log_info_messages)
    assert any("Speedtest failed with return code 2 (attempt 1/3)" in m for m in log_warn_messages)
    assert any("Speedtest failed with return code 2 (attempt 2/3)" in m for m in log_warn_messages)


def test_complete_failure_produces_exactly_one_counted_error(monkeypatch, tmp_path):
    error_log_path = tmp_path / "errors.log"
    config = {
        "paths": {
            "speedtest_exe": "speedtest",
            "error_log": str(error_log_path),
            "log_directory": str(tmp_path / "Log"),
        },
        "speedtest": {
            "server_id": "71403",
            "max_retries": 3,
            "retry_delay_seconds": 0,
            "timeout_seconds": 10,
        },
    }

    monkeypatch.setattr(CheckSpeed, "resolve_speedtest_executable", lambda _cfg: "/usr/bin/speedtest")
    monkeypatch.setattr(CheckSpeed, "detect_speedtest_provider", lambda _exe: "ookla")
    monkeypatch.setattr(CheckSpeed.time, "sleep", lambda _s: None)

    def mock_run(cmd, capture_output=True, text=True, timeout=10):
        return subprocess.CompletedProcess(
            cmd,
            returncode=2,
            stdout="",
            stderr="No connection",
        )

    monkeypatch.setattr(CheckSpeed.subprocess, "run", mock_run)

    result = CheckSpeed.run_speedtest_with_retry(config)
    assert result is None

    # Error log exists and contains exactly 1 error entry
    assert error_log_path.exists()
    lines = [line.strip() for line in error_log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert "] ERROR: Speedtest failed after 3 attempts" in lines[0]

    # Health check parses exactly 1 error
    health_result = health_check.check_error_log(config)
    assert health_result["recent_errors"] == 1


def test_behavior_without_configured_server_remains_unchanged(monkeypatch, tmp_path):
    error_log_path = tmp_path / "errors.log"
    config = {
        "paths": {
            "speedtest_exe": "speedtest",
            "error_log": str(error_log_path),
            "log_directory": str(tmp_path / "Log"),
        },
        "speedtest": {
            "server_id": "",
            "max_retries": 3,
            "retry_delay_seconds": 0,
            "timeout_seconds": 10,
        },
    }

    commands_executed = []
    log_info_messages = []

    def mock_info(msg, *args):
        log_info_messages.append(msg % args if args else msg)

    monkeypatch.setattr(CheckSpeed.log, "info", mock_info)
    monkeypatch.setattr(CheckSpeed, "resolve_speedtest_executable", lambda _cfg: "/usr/bin/speedtest")
    monkeypatch.setattr(CheckSpeed, "detect_speedtest_provider", lambda _exe: "ookla")
    monkeypatch.setattr(CheckSpeed.time, "sleep", lambda _s: None)

    ookla_success_payload = {
        "type": "result",
        "download": {"bandwidth": 10000000},
        "upload": {"bandwidth": 5000000},
        "ping": {"latency": 10.0, "jitter": 1.0},
        "packetLoss": 0.0,
        "isp": "Test ISP",
        "interface": {"externalIp": "1.2.3.4"},
        "server": {
            "id": 9999,
            "name": "AutoServer",
            "location": "AutoCity",
            "country": "AutoCountry",
        },
        "result": {"url": "https://www.speedtest.net/result/c/9999"},
    }

    def mock_run(cmd, capture_output=True, text=True, timeout=10):
        commands_executed.append(list(cmd))
        if len(commands_executed) < 2:
            return subprocess.CompletedProcess(cmd, returncode=2, stdout="", stderr="transient failure")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=json.dumps(ookla_success_payload), stderr="")

    monkeypatch.setattr(CheckSpeed.subprocess, "run", mock_run)

    result = CheckSpeed.run_speedtest_with_retry(config)
    assert result is not None
    assert result["server_id"] == "9999"

    # All commands omit --server-id
    for cmd in commands_executed:
        assert "--server-id" not in cmd

    assert any("Automatic server selection" in m for m in log_info_messages)
    assert not any("Preferred server" in m for m in log_info_messages)
    assert not any("Fallback server selected" in m for m in log_info_messages)


def test_log_result_preserves_actual_server_metadata(tmp_path):
    log_dir = tmp_path / "Log"
    config = {
        "paths": {
            "log_directory": str(log_dir),
            "error_log": str(tmp_path / "errors.log"),
        },
        "email": {"send_realtime_alerts": False},
        "thresholds": {"download_mbps": 50, "upload_mbps": 20, "ping_ms": 50, "packet_loss_percent": 2.0},
    }

    result = {
        "download_bps": 100_000_000,
        "upload_bps": 50_000_000,
        "ping_ms": 15.2,
        "jitter_ms": 2.1,
        "packet_loss_percent": 0.0,
        "server_name": "Blacknight",
        "server_location": "Dublin",
        "server_id": "4604",
        "isp": "Sky Broadband",
        "external_ip": "1.2.3.4",
        "result_url": "https://www.speedtest.net/result/c/12345",
    }

    CheckSpeed.log_result(config, result)

    log_files = list(log_dir.glob("speed_log_week_*.txt"))
    assert len(log_files) == 1
    content = log_files[0].read_text(encoding="utf-8")
    assert "Server: Blacknight – Dublin (id: 4604)" in content
