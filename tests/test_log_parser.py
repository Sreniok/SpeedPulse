"""Unit tests for log_parser — covers multi-line and legacy pipe formats."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from log_parser import _extract_float, _parse_pipe_line, load_all_log_entries, parse_weekly_log_file

# ---------------------------------------------------------------------------
# _extract_float
# ---------------------------------------------------------------------------

class TestExtractFloat:
    def test_integer(self):
        assert _extract_float("42") == 42.0

    def test_decimal(self):
        assert _extract_float("3.14") == 3.14

    def test_with_units(self):
        assert _extract_float("550.3 Mbps") == 550.3

    def test_no_number(self):
        assert _extract_float("none") == 0.0

    def test_custom_default(self):
        assert _extract_float("---", default=-1.0) == -1.0


# ---------------------------------------------------------------------------
# _parse_pipe_line  (legacy one-line format)
# ---------------------------------------------------------------------------

class TestParsePipeLine:
    def test_basic(self):
        line = "2025-12-05 10:30:00 | Download: 500.5 Mbps | Upload: 100.2 Mbps | Ping: 15 ms"
        result = _parse_pipe_line(line)
        assert result is not None
        assert result["timestamp"] == datetime(2025, 12, 5, 10, 30)
        assert result["download_mbps"] == 500.5
        assert result["upload_mbps"] == 100.2
        assert result["ping_ms"] == 15.0
        assert result["jitter_ms"] == 0.0
        assert result["packet_loss_percent"] == 0.0

    def test_with_jitter_and_loss(self):
        line = "2025-12-05 10:30:00 | Download: 500 Mbps | Upload: 100 Mbps | Ping: 15 ms | Jitter: 2.3 ms | Loss: 0.5%"
        result = _parse_pipe_line(line)
        assert result is not None
        assert result["jitter_ms"] == 2.3
        assert result["packet_loss_percent"] == 0.5

    def test_too_few_parts(self):
        assert _parse_pipe_line("only | two") is None

    def test_bad_timestamp(self):
        assert _parse_pipe_line("not-a-date | Download: 1 | Upload: 2 | Ping: 3") is None


# ---------------------------------------------------------------------------
# parse_weekly_log_file  (multi-line format)
# ---------------------------------------------------------------------------

MULTI_LINE_BLOCK = """\
====================  09-03-2026 Speed Test Result  ====================
Date: 09-03-2026
Time: 14:23
Server: Preston - United Kingdom (id: 41075)
ISP: Sky Broadband
Ping: 28.12 ms
Jitter: 0.00 ms
Packet Loss: 0.00%
Download: 293.86 Mbps
Upload: 104.4 Mbps
Result URL: None
"""

TWO_BLOCKS = """\
Date: 01-01-2026
Time: 08:00
Server: London
ISP: BT
Ping: 10 ms
Jitter: 1 ms
Packet Loss: 0%
Download: 400 Mbps
Upload: 50 Mbps

Date: 01-01-2026
Time: 16:00
Server: Manchester
ISP: Virgin
Ping: 12 ms
Jitter: 2 ms
Packet Loss: 0.5%
Download: 600 Mbps
Upload: 80 Mbps
"""

LEGACY_PIPE = """\
2025-06-01 08:00:00 | Download: 200 Mbps | Upload: 50 Mbps | Ping: 20 ms
2025-06-01 16:00:00 | Download: 300 Mbps | Upload: 60 Mbps | Ping: 18 ms
"""

MIXED_FORMAT = """\
2025-06-01 08:00:00 | Download: 200 Mbps | Upload: 50 Mbps | Ping: 20 ms

Date: 02-06-2025
Time: 10:00
Server: London
ISP: BT
Ping: 10 ms
Jitter: 1 ms
Packet Loss: 0%
Download: 500 Mbps
Upload: 100 Mbps
"""


class TestParseWeeklyLogFileMultiLine:
    def test_single_block(self, tmp_path: Path):
        log = tmp_path / "week.txt"
        log.write_text(MULTI_LINE_BLOCK, encoding="utf-8")
        entries = parse_weekly_log_file(log)
        assert len(entries) == 1
        e = entries[0]
        assert e["timestamp"] == datetime(2026, 3, 9, 14, 23)
        assert e["download_mbps"] == 293.86
        assert e["upload_mbps"] == 104.4
        assert e["ping_ms"] == 28.12
        assert e["jitter_ms"] == 0.0
        assert e["packet_loss_percent"] == 0.0
        assert e["server"] == "Preston - United Kingdom (id: 41075)"
        assert e["isp"] == "Sky Broadband"

    def test_two_blocks(self, tmp_path: Path):
        log = tmp_path / "week.txt"
        log.write_text(TWO_BLOCKS, encoding="utf-8")
        entries = parse_weekly_log_file(log)
        assert len(entries) == 2
        assert entries[0]["timestamp"] < entries[1]["timestamp"]
        assert entries[0]["download_mbps"] == 400.0
        assert entries[1]["download_mbps"] == 600.0

    def test_missing_file_returns_empty(self, tmp_path: Path):
        missing = tmp_path / "no_such_file.txt"
        assert parse_weekly_log_file(missing) == []

    def test_empty_file(self, tmp_path: Path):
        log = tmp_path / "empty.txt"
        log.write_text("", encoding="utf-8")
        assert parse_weekly_log_file(log) == []

    def test_incomplete_block_missing_date(self, tmp_path: Path):
        """A block that has no Date: line should be skipped."""
        content = "Time: 10:00\nServer: X\nISP: Y\nPing: 5 ms\nJitter: 1 ms\nPacket Loss: 0%\nDownload: 100 Mbps\nUpload: 50 Mbps\n"
        log = tmp_path / "bad.txt"
        log.write_text(content, encoding="utf-8")
        assert parse_weekly_log_file(log) == []


class TestParseWeeklyLogFileLegacy:
    def test_pipe_format(self, tmp_path: Path):
        log = tmp_path / "week.txt"
        log.write_text(LEGACY_PIPE, encoding="utf-8")
        entries = parse_weekly_log_file(log)
        assert len(entries) == 2
        assert entries[0]["download_mbps"] == 200.0
        assert entries[1]["download_mbps"] == 300.0


class TestParseWeeklyLogFileMixed:
    def test_mixed_formats(self, tmp_path: Path):
        log = tmp_path / "week.txt"
        log.write_text(MIXED_FORMAT, encoding="utf-8")
        entries = parse_weekly_log_file(log)
        assert len(entries) == 2
        # Should be sorted by timestamp; the pipe line (2025-06-01 08:00)
        # comes before the multi-line block (2025-06-02 10:00).
        assert entries[0]["download_mbps"] == 200.0
        assert entries[1]["download_mbps"] == 500.0


# ---------------------------------------------------------------------------
# load_all_log_entries
# ---------------------------------------------------------------------------

class TestLoadAllLogEntries:
    def test_loads_multiple_files(self, tmp_path: Path):
        log_dir = tmp_path / "Log"
        log_dir.mkdir()
        (log_dir / "speed_log_week_1.txt").write_text(
            "Date: 01-01-2026\nTime: 08:00\nServer: A\nISP: B\nPing: 5 ms\nJitter: 1 ms\nPacket Loss: 0%\nDownload: 100 Mbps\nUpload: 20 Mbps\n",
            encoding="utf-8",
        )
        (log_dir / "speed_log_week_2.txt").write_text(
            "Date: 08-01-2026\nTime: 08:00\nServer: A\nISP: B\nPing: 5 ms\nJitter: 1 ms\nPacket Loss: 0%\nDownload: 200 Mbps\nUpload: 40 Mbps\n",
            encoding="utf-8",
        )
        entries = load_all_log_entries(log_dir)
        assert len(entries) == 2
        assert entries[0]["download_mbps"] == 100.0
        assert entries[1]["download_mbps"] == 200.0

    def test_empty_directory(self, tmp_path: Path):
        log_dir = tmp_path / "Empty"
        log_dir.mkdir()
        assert load_all_log_entries(log_dir) == []

    def test_ignores_non_matching_files(self, tmp_path: Path):
        log_dir = tmp_path / "Log"
        log_dir.mkdir()
        (log_dir / "notes.txt").write_text("not a log", encoding="utf-8")
        assert load_all_log_entries(log_dir) == []
