#!/usr/bin/env python3
"""Internal scheduler service for Docker deployments."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from backup_manager import run_scheduled_backup

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
CRON_LOG = SCRIPT_DIR / "cron.log"
CONFIG_CHECK_INTERVAL = 10  # seconds between config change checks


def log(message: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line, flush=True)
    with CRON_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_hhmm(value: str, default: str) -> tuple[int, int]:
    source = value.strip() if value else default
    hour_str, minute_str = source.split(":", 1)
    return int(hour_str), int(minute_str)


def parse_weekly_schedule(value: str) -> tuple[str, int, int]:
    # Expected format: "Monday 08:00"
    day_part, time_part = value.strip().split(" ", 1)
    day_key = day_part.lower()[:3]
    hour, minute = parse_hhmm(time_part, "08:00")
    return day_key, hour, minute


def run_script(script_name: str) -> None:
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        log(f"Script not found: {script_name}")
        return

    log(f"Starting job: {script_name}")
    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=SCRIPT_DIR,
        capture_output=True,
        text=True,
    )

    if result.stdout:
        for line in result.stdout.strip().splitlines():
            log(f"{script_name} | {line}")

    if result.stderr:
        for line in result.stderr.strip().splitlines():
            log(f"{script_name} | STDERR | {line}")

    if result.returncode == 0:
        log(f"Job finished successfully: {script_name}")
    else:
        log(f"Job failed ({result.returncode}): {script_name}")


def configure_scheduler(scheduler: BlockingScheduler, config: dict) -> None:
    scheduling = config.get("scheduling", {})
    notifications = config.get("notifications", {})

    # Run speed tests at configured times.
    test_times = scheduling.get("test_times", ["08:00", "16:00", "22:00"])
    for index, test_time in enumerate(test_times, start=1):
        hour, minute = parse_hhmm(test_time, "08:00")
        scheduler.add_job(
            run_script,
            trigger=CronTrigger(hour=hour, minute=minute),
            args=["CheckSpeed.py"],
            id=f"speedtest_{index}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
        log(f"Scheduled speed test #{index} at {hour:02d}:{minute:02d}")

    # Weekly report schedule.
    if notifications.get("weekly_report_enabled", True):
        weekly_report = scheduling.get("weekly_report_time", "Monday 08:00")
        day_of_week, hour, minute = parse_weekly_schedule(weekly_report)
        scheduler.add_job(
            run_script,
            trigger=CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute),
            args=["SendWeeklyReport.py"],
            id="weekly_report",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=21600,
        )
        log(f"Scheduled weekly report on {day_of_week} at {hour:02d}:{minute:02d}")
    else:
        log("Weekly report schedule is disabled in settings")

    # Daily health check.
    health_time = os.getenv("HEALTH_CHECK_TIME", "07:00")
    health_hour, health_minute = parse_hhmm(health_time, "07:00")
    scheduler.add_job(
        run_script,
        trigger=CronTrigger(hour=health_hour, minute=health_minute),
        args=["health_check.py"],
        id="health_check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    log(f"Scheduled health check at {health_hour:02d}:{health_minute:02d}")

    # Monthly log rotation.
    rotation_time = os.getenv("LOG_ROTATION_TIME", "02:00")
    rotation_hour, rotation_minute = parse_hhmm(rotation_time, "02:00")
    scheduler.add_job(
        run_script,
        trigger=CronTrigger(day=1, hour=rotation_hour, minute=rotation_minute),
        args=["rotate_logs.py"],
        id="rotate_logs",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    log(f"Scheduled monthly log rotation at {rotation_hour:02d}:{rotation_minute:02d} on day 1")

    # Scheduled automatic backups.
    backup_cfg = config.get("backup", {})
    if backup_cfg.get("scheduled_backup_enabled", False):
        backup_time = backup_cfg.get("scheduled_backup_time", "03:00")
        backup_frequency = backup_cfg.get("scheduled_backup_frequency", "daily")
        backup_hour, backup_minute = parse_hhmm(backup_time, "03:00")

        def _run_backup_job() -> None:
            result = run_scheduled_backup()
            log(f"backup | {result}")

        trigger_kwargs: dict = {"hour": backup_hour, "minute": backup_minute}
        if backup_frequency == "weekly":
            trigger_kwargs["day_of_week"] = "sun"
        elif backup_frequency == "monthly":
            trigger_kwargs["day"] = 1

        scheduler.add_job(
            _run_backup_job,
            trigger=CronTrigger(**trigger_kwargs),
            id="scheduled_backup",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        log(f"Scheduled {backup_frequency} backup at {backup_hour:02d}:{backup_minute:02d}")
    else:
        if scheduler.get_job("scheduled_backup"):
            scheduler.remove_job("scheduled_backup")
            log("Scheduled backup disabled — removed job")

    # One-shot contract expiry reminder scheduled for (end_date - reminder_days).
    contract_cfg = config.get("contract", {}).get("current", {})
    if contract_cfg.get("reminder_enabled", False):
        end_date_str = contract_cfg.get("end_date", "").strip()
        reminder_days = int(contract_cfg.get("reminder_days", 31))
        if end_date_str:
            try:
                from datetime import timedelta
                end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
                reminder_dt = end_dt - timedelta(days=reminder_days)
                # Set to 09:00 on the reminder date.
                reminder_dt = reminder_dt.replace(hour=9, minute=0, second=0)
                if reminder_dt > datetime.now():
                    scheduler.add_job(
                        run_script,
                        trigger=DateTrigger(run_date=reminder_dt),
                        args=["contract_reminder.py"],
                        id="contract_reminder",
                        replace_existing=True,
                        max_instances=1,
                        misfire_grace_time=86400,
                    )
                    log(f"Scheduled contract reminder for {reminder_dt.strftime('%Y-%m-%d %H:%M')} ({reminder_days} days before contract end)")
                else:
                    log(f"Contract reminder date {reminder_dt.strftime('%Y-%m-%d')} is in the past — skipping")
                    if scheduler.get_job("contract_reminder"):
                        scheduler.remove_job("contract_reminder")
            except ValueError as exc:
                log(f"Invalid contract end_date '{end_date_str}': {exc}")
        else:
            log("Contract reminder enabled but no end date set — skipping")
    else:
        # Remove the job if it exists but reminder was disabled.
        if scheduler.get_job("contract_reminder"):
            scheduler.remove_job("contract_reminder")
            log("Contract reminder disabled — removed scheduled job")


def main() -> None:
    timezone = os.getenv("APP_TIMEZONE", os.getenv("TZ", "UTC"))
    log(f"Starting scheduler service (timezone={timezone})")

    config = load_config()
    scheduler = BlockingScheduler(timezone=timezone)
    configure_scheduler(scheduler, config)

    # Track config file modification time for hot-reload.
    last_mtime = CONFIG_PATH.stat().st_mtime

    def check_config_reload() -> None:
        nonlocal last_mtime
        try:
            current_mtime = CONFIG_PATH.stat().st_mtime
            if current_mtime != last_mtime:
                last_mtime = current_mtime
                log("Config change detected — reloading scheduler jobs")
                new_config = load_config()
                configure_scheduler(scheduler, new_config)
                log("Scheduler jobs reloaded successfully")
        except Exception as exc:
            log(f"Config reload check failed: {exc}")

    scheduler.add_job(
        check_config_reload,
        trigger="interval",
        seconds=CONFIG_CHECK_INTERVAL,
        id="config_watcher",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    log(f"Config watcher active (checking every {CONFIG_CHECK_INTERVAL}s)")

    # Optional startup run for quick smoke test in new environments.
    if os.getenv("RUN_STARTUP_SPEEDTEST", "false").lower() == "true":
        run_script("CheckSpeed.py")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log("Scheduler service stopped")


if __name__ == "__main__":
    main()
