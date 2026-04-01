from __future__ import annotations

from datetime import datetime

from reporting import build_contract_report_html, build_report_html


def test_build_report_html_includes_inline_charts() -> None:
    config = {
        "account": {"name": "Test Account", "number": "1234"},
        "thresholds": {
            "download_mbps": 500,
            "upload_mbps": 80,
            "ping_ms": 20,
            "packet_loss_percent": 1,
        },
        "notifications": {"report_theme_id": "github-dark"},
    }
    entries = [
        {
            "timestamp": datetime(2026, 3, 19, 10, 58),
            "download_mbps": 873.13,
            "upload_mbps": 101.94,
            "ping_ms": 5.14,
            "jitter_ms": 0.38,
            "packet_loss_percent": 0.0,
            "server": "BT Ireland - Dublin",
            "source": "manual",
        },
        {
            "timestamp": datetime(2026, 3, 19, 16, 0),
            "download_mbps": 865.12,
            "upload_mbps": 102.0,
            "ping_ms": 6.76,
            "jitter_ms": 0.74,
            "packet_loss_percent": 0.0,
            "server": "Blacknight - Dublin",
            "source": "scheduled",
        },
    ]

    html = build_report_html(
        config,
        entries,
        report_title="SpeedPulse Performance Report",
        range_label="Last 90 days",
        generated_at=datetime(2026, 3, 19, 16, 41),
    )

    assert 'class="charts-grid"' in html
    assert "Download / Upload" in html
    assert "Ping / Jitter" in html
    assert "Threshold breaches" in html
    assert 'class="report-chart"' in html
    assert "Download floor" in html
    assert "Ping ceiling" in html
    assert "Theme github-dark" not in html


def test_build_report_html_shows_empty_chart_state_without_entries() -> None:
    config = {
        "account": {"name": "Test Account", "number": "1234"},
        "thresholds": {
            "download_mbps": 500,
            "upload_mbps": 80,
            "ping_ms": 20,
            "packet_loss_percent": 1,
        },
    }

    html = build_report_html(
        config,
        [],
        report_title="SpeedPulse Performance Report",
        range_label="Today",
        generated_at=datetime(2026, 3, 19, 16, 41),
    )

    assert html.count("No test data available for this range.") == 3
    assert "No test data in this range yet." in html


def test_build_contract_report_html_includes_contract_summary_cards() -> None:
    config = {
        "account": {"name": "Test Account", "number": "1234"},
        "notifications": {"report_theme_id": "github-dark"},
    }
    contract = {
        "provider": "Sky - Dublin",
        "account_name": "Test Account",
        "account_number": "1234",
        "ip_address": "1.2.3.4",
        "start_date": "2026-03-01",
        "end_date": "2026-03-31",
        "download_mbps": 1000,
        "upload_mbps": 100,
    }
    summary = {
        "total_tests": 42,
        "download": {"avg": 921.38, "min": 500.0, "max": 942.0},
        "upload": {"avg": 101.32, "min": 80.0, "max": 102.1},
        "ping": {"avg": 5.83, "min": 4.8, "max": 8.1},
        "jitter": {"avg": 0.18, "min": 0.05, "max": 0.8},
        "packet_loss": {"avg": 0.0, "min": 0.0, "max": 0.0},
        "sources": {"scheduled": 39, "manual": 3},
        "breaches": {"download": 2, "upload": 0, "ping": 1, "loss": 0, "total": 3},
        "latest_test_at": "2026-03-31 22:00",
    }

    html = build_contract_report_html(
        config,
        contract,
        summary,
        generated_at=datetime(2026, 4, 1, 14, 50),
    )

    assert "Contract performance breakdown" in html
    assert "Sky - Dublin" in html
    assert "921.38 Mbps" in html
    assert "5.83 ms" in html
    assert "Threshold breaches" in html
