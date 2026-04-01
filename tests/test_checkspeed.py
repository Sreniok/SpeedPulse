"""Unit coverage for the speedtest runner."""

from __future__ import annotations

import CheckSpeed


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
