#!/usr/bin/env python3
"""
CheckSpeed.py - Main speed test script
Runs speedtest and logs results with retry logic and alerting.
Supports:
- Ookla CLI (`speedtest --format=json`)
- speedtest-cli (`speedtest-cli --json`)
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from config_loader import load_json_config_or_exit
from logger_setup import get_logger
from state_store import record_speedtest_completion

log = get_logger("CheckSpeed")


def load_config():
    """Load configuration from config.json"""
    return load_json_config_or_exit(
        __file__,
        missing_message="Configuration file not found: config.json",
        on_missing=log.error,
        exit_code=1,
    )


def write_error_log(config, message):
    """Write error to error log"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] ERROR: {message}\n"
    script_dir = Path(__file__).parent
    error_log = script_dir / config["paths"]["error_log"]

    with open(error_log, "a", encoding="utf-8") as f:
        f.write(log_entry)

    log.warning("%s", message)


def resolve_speedtest_executable(config):
    """Resolve speedtest executable with fallback order."""
    configured = os.getenv("SPEEDTEST_EXE", config["paths"].get("speedtest_exe", "speedtest"))
    candidates = [
        configured,
        "speedtest",
        "speedtest-cli",
        "/usr/bin/speedtest",
        "/usr/local/bin/speedtest",
        "/usr/bin/speedtest-cli",
        "/usr/local/bin/speedtest-cli",
    ]

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    raise FileNotFoundError("No speedtest executable found in PATH")


def detect_speedtest_provider(speedtest_exe):
    """Detect whether executable is official Ookla CLI or python speedtest-cli."""
    probe_commands = ([speedtest_exe, "--version"], [speedtest_exe, "--help"])

    combined_output = ""
    for probe in probe_commands:
        try:
            result = subprocess.run(
                probe,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except Exception:
            continue

        combined_output += f"\n{result.stdout}\n{result.stderr}"

    fingerprint = combined_output.lower()
    if "speedtest by ookla" in fingerprint or "--accept-license" in fingerprint:
        return "ookla"
    if "speedtest-cli" in fingerprint:
        return "speedtest-cli"

    # Safe fallback by executable name.
    exe_name = Path(speedtest_exe).name.lower()
    if exe_name == "speedtest-cli":
        return "speedtest-cli"
    if exe_name == "speedtest":
        return "ookla"

    raise ValueError(f"Unable to detect speedtest provider for executable: {speedtest_exe}")


def resolve_server_id(config):
    """Resolve optional fixed server id from env/config."""
    raw_value = os.getenv("SPEEDTEST_SERVER_ID", "")
    if not raw_value:
        configured_value = config.get("speedtest", {}).get("server_id", "")
        raw_value = str(configured_value).strip() if configured_value is not None else ""

    if not raw_value:
        return None

    if not raw_value.isdigit():
        raise ValueError("Configured speedtest server_id must be numeric")

    return raw_value


def build_speedtest_command(speedtest_exe, provider, server_id=None):
    """Build command for the selected speedtest binary."""
    if provider == "ookla":
        cmd = [speedtest_exe, "--accept-license", "--accept-gdpr", "--format=json"]
        if server_id:
            cmd.extend(["--server-id", server_id])
        return cmd

    # speedtest-cli
    cmd = [speedtest_exe, "--json"]
    if server_id:
        cmd.extend(["--server", server_id])
    return cmd


def normalize_speedtest_result(raw_data):
    """Normalize output from either speedtest-cli or Ookla CLI."""
    # Ookla format
    if isinstance(raw_data.get("download"), dict) and "bandwidth" in raw_data["download"]:
        server = raw_data.get("server", {})
        ping_data = raw_data.get("ping", {})

        return {
            "download_bps": float(raw_data["download"].get("bandwidth", 0.0)) * 8,
            "upload_bps": float(raw_data.get("upload", {}).get("bandwidth", 0.0)) * 8,
            "ping_ms": float(ping_data.get("latency", 0.0)),
            "jitter_ms": float(ping_data.get("jitter", 0.0)),
            "packet_loss_percent": float(raw_data.get("packetLoss", 0.0) or 0.0),
            "server_name": server.get("name", "Unknown"),
            "server_location": server.get("location") or server.get("country", "Unknown"),
            "server_id": str(server.get("id", "N/A")),
            "isp": raw_data.get("isp", "Unknown"),
            "result_url": raw_data.get("result", {}).get("url") or "N/A",
        }

    # speedtest-cli format
    if all(key in raw_data for key in ["download", "upload", "ping"]):
        server = raw_data.get("server", {})
        client = raw_data.get("client", {})

        return {
            "download_bps": float(raw_data.get("download", 0.0)),
            "upload_bps": float(raw_data.get("upload", 0.0)),
            "ping_ms": float(raw_data.get("ping", 0.0)),
            "jitter_ms": 0.0,
            "packet_loss_percent": float(raw_data.get("packetLoss", 0.0) or 0.0),
            "server_name": server.get("name", "Unknown"),
            "server_location": server.get("country", "Unknown"),
            "server_id": str(server.get("id", "N/A")),
            "isp": client.get("isp", "Unknown"),
            "result_url": raw_data.get("share") or "N/A",
        }

    raise ValueError("Unsupported speedtest JSON format")


def run_speedtest_with_retry(config):
    """Run speedtest with retry logic"""
    max_retries = config["speedtest"]["max_retries"]
    retry_delay = config["speedtest"]["retry_delay_seconds"]
    timeout = config["speedtest"]["timeout_seconds"]

    log.info("Preparing speedtest engine...")

    try:
        speedtest_exe = resolve_speedtest_executable(config)
        provider = detect_speedtest_provider(speedtest_exe)
        server_id = resolve_server_id(config)
    except FileNotFoundError:
        write_error_log(config, "No speedtest executable found. Install `speedtest` or `speedtest-cli`.")
        log.error("No speedtest executable found in PATH.")
        sys.exit(1)
    except ValueError as e:
        write_error_log(config, str(e))
        log.error("%s", e)
        sys.exit(1)

    cmd = build_speedtest_command(speedtest_exe, provider, server_id=server_id)
    provider_label = "Ookla CLI" if provider == "ookla" else "speedtest-cli"
    server_label = f"Selected server #{server_id}" if server_id else "Automatic server selection"

    log.info("Using %s via %s", provider_label, Path(speedtest_exe).name)
    log.info("%s", server_label)

    for attempt in range(1, max_retries + 1):
        try:
            log.info(
                "Running %s via %s%s (attempt %d/%d)",
                provider_label,
                Path(speedtest_exe).name,
                f", server_id={server_id}" if server_id else "",
                attempt,
                max_retries,
            )
            log.info("Measuring download and upload throughput...")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode == 0 and result.stdout:
                log.info("Speedtest finished, validating result payload...")
                raw_data = json.loads(result.stdout)
                normalized = normalize_speedtest_result(raw_data)

                # Validate required normalized fields
                if all(key in normalized for key in ["download_bps", "upload_bps", "ping_ms"]):
                    log.info("Speedtest completed successfully")
                    return normalized

                write_error_log(config, f"Speedtest returned incomplete normalized data (attempt {attempt})")
            else:
                write_error_log(config, f"Speedtest failed with return code {result.returncode} (attempt {attempt})")
                if result.stderr:
                    write_error_log(config, f"Error output: {result.stderr.strip()}")

        except subprocess.TimeoutExpired:
            write_error_log(config, f"Speedtest timed out after {timeout} seconds (attempt {attempt})")
        except json.JSONDecodeError as e:
            write_error_log(config, f"Failed to parse speedtest JSON output (attempt {attempt}): {e}")
        except Exception as e:
            write_error_log(config, f"Speedtest failed with error: {e} (attempt {attempt})")

        if attempt < max_retries:
            log.info("Waiting %d seconds before retry...", retry_delay)
            time.sleep(retry_delay)

    write_error_log(config, f"Speedtest failed after {max_retries} attempts")
    return None


def get_week_number():
    """Get ISO week number"""
    return datetime.now().isocalendar()[1]


def log_result(config, result):
    """Log speed test result to weekly log file"""
    timestamp = datetime.now()
    week_num = get_week_number()

    script_dir = Path(__file__).parent
    log_dir = script_dir / config["paths"]["log_directory"]
    log_dir.mkdir(parents=True, exist_ok=True)

    log_path = log_dir / f"speed_log_week_{week_num}.txt"

    download_mbps = round(result["download_bps"] / 1_000_000, 2)
    upload_mbps = round(result["upload_bps"] / 1_000_000, 2)
    ping_ms = round(result["ping_ms"], 2)
    jitter_ms = round(result.get("jitter_ms", 0.0), 2)
    packet_loss = round(result.get("packet_loss_percent", 0.0), 2)

    header = f"{'=' * 20}  {timestamp.strftime('%d-%m-%Y')} Speed Test Result  {'=' * 20}\n"
    entry = f"""Date: {timestamp.strftime('%d-%m-%Y')}
Time: {timestamp.strftime('%H:%M')}
Server: {result['server_name']} – {result['server_location']} (id: {result['server_id']})
ISP: {result['isp']}
Ping: {ping_ms} ms
Jitter: {jitter_ms} ms
Packet Loss: {packet_loss}%
Download: {download_mbps} Mbps
Upload: {upload_mbps} Mbps
Result URL: {result['result_url']}

"""

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(header)
        f.write(entry)

    log.info("Speed test logged to: %s", log_path)

    return download_mbps, upload_mbps, ping_ms, jitter_ms, packet_loss


def check_thresholds_and_alert(config, download, upload, ping, packet_loss):
    """Check thresholds and send alert if needed"""
    if not config["email"]["send_realtime_alerts"]:
        return

    alert_script = Path(__file__).parent / "SendAlert.py"
    if not alert_script.exists():
        return

    violations = []

    if download < config["thresholds"]["download_mbps"]:
        violations.append(f"Download: {download} Mbps (threshold: {config['thresholds']['download_mbps']} Mbps)")

    if upload < config["thresholds"]["upload_mbps"]:
        violations.append(f"Upload: {upload} Mbps (threshold: {config['thresholds']['upload_mbps']} Mbps)")

    if ping > config["thresholds"]["ping_ms"]:
        violations.append(f"Ping: {ping} ms (threshold: {config['thresholds']['ping_ms']} ms)")

    if packet_loss > config["thresholds"]["packet_loss_percent"]:
        violations.append(
            f"Packet Loss: {packet_loss}% (threshold: {config['thresholds']['packet_loss_percent']}%)"
        )

    if violations:
        try:
            cmd = [
                sys.executable,
                str(alert_script),
                str(download),
                str(upload),
                str(ping),
                str(packet_loss),
            ] + violations

            subprocess.run(cmd, check=False)
        except Exception as e:
            write_error_log(config, f"Failed to send alert: {e}")


def display_results(config, download, upload, ping, jitter, packet_loss):
    """Display formatted test results"""
    thresholds = config["thresholds"]

    log.info("Test Results:")

    def get_status(value, threshold, inverse=False):
        if inverse:
            return "✓" if value <= threshold else "✗"
        return "✓" if value >= threshold else "✗"

    log.info("  Download: %s Mbps %s", download, get_status(download, thresholds['download_mbps']))
    log.info("  Upload: %s Mbps %s", upload, get_status(upload, thresholds['upload_mbps']))
    log.info("  Ping: %s ms %s", ping, get_status(ping, thresholds['ping_ms'], inverse=True))
    log.info("  Jitter: %s ms", jitter)
    log.info(
        "  Packet Loss: %s%% %s",
        packet_loss,
        get_status(packet_loss, thresholds['packet_loss_percent'], inverse=True),
    )


def persist_completion_event(success):
    source = os.getenv("SPEEDTEST_RUN_SOURCE", "scheduled").strip().lower()
    if source not in {"manual", "scheduled"}:
        source = "scheduled"

    status = "success" if success else "failed"
    try:
        record_speedtest_completion(status=status, source=source)
    except Exception as e:
        log.warning("Failed to persist completion marker: %s", e)


def main():
    """Main execution"""
    config = load_config()

    result = run_speedtest_with_retry(config)

    if result is None:
        log.error("Failed to complete speedtest. Check error log: %s", config['paths']['error_log'])
        sys.exit(1)

    log.info("Saving result to log...")
    download, upload, ping, jitter, packet_loss = log_result(config, result)

    log.info("Evaluating alert thresholds...")
    check_thresholds_and_alert(config, download, upload, ping, packet_loss)
    log.info("Rendering result summary...")
    display_results(config, download, upload, ping, jitter, packet_loss)

    log.info("Speed test completed successfully!")


if __name__ == "__main__":
    exit_code = 0
    try:
        main()
    except SystemExit as e:
        if e.code is None:
            exit_code = 0
        elif isinstance(e.code, int):
            exit_code = e.code
        else:
            exit_code = 1
    except KeyboardInterrupt:
        log.warning("Speed test cancelled by user.")
        exit_code = 1
    except Exception as e:
        log.exception("Unexpected error: %s", e)
        exit_code = 1
    finally:
        persist_completion_event(exit_code == 0)

    sys.exit(exit_code)
