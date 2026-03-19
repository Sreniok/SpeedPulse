from __future__ import annotations

from datetime import datetime

from reporting import build_report_html


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

    assert 'class="chart-grid"' in html
    assert "Throughput trend" in html
    assert "Latency trend" in html
    assert 'class="report-chart"' in html
    assert "Download floor" in html
    assert "Ping ceiling" in html


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

    assert html.count("No test data available for this range.") == 2
    assert "No test data in this range yet." in html
