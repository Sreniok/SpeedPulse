"""Coverage for scheduler bootstrap helpers."""

from __future__ import annotations

import importlib
import sys
import types
from datetime import UTC, datetime


def load_scheduler_service_module():
    """Import scheduler_service without requiring full backup dependencies."""
    sys.modules.pop("scheduler_service", None)
    backup_manager_stub = types.ModuleType("backup_manager")
    backup_manager_stub.run_scheduled_backup = lambda: "ok"
    apscheduler_stub = types.ModuleType("apscheduler")
    schedulers_stub = types.ModuleType("apscheduler.schedulers")
    blocking_stub = types.ModuleType("apscheduler.schedulers.blocking")
    triggers_stub = types.ModuleType("apscheduler.triggers")
    cron_stub = types.ModuleType("apscheduler.triggers.cron")
    date_stub = types.ModuleType("apscheduler.triggers.date")

    class DummyBlockingScheduler:
        pass

    class DummyCronTrigger:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class DummyDateTrigger:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    blocking_stub.BlockingScheduler = DummyBlockingScheduler
    cron_stub.CronTrigger = DummyCronTrigger
    date_stub.DateTrigger = DummyDateTrigger

    sys.modules["backup_manager"] = backup_manager_stub
    sys.modules["apscheduler"] = apscheduler_stub
    sys.modules["apscheduler.schedulers"] = schedulers_stub
    sys.modules["apscheduler.schedulers.blocking"] = blocking_stub
    sys.modules["apscheduler.triggers"] = triggers_stub
    sys.modules["apscheduler.triggers.cron"] = cron_stub
    sys.modules["apscheduler.triggers.date"] = date_stub
    try:
        return importlib.import_module("scheduler_service")
    finally:
        sys.modules.pop("scheduler_service", None)
        sys.modules.pop("backup_manager", None)
        sys.modules.pop("apscheduler", None)
        sys.modules.pop("apscheduler.schedulers", None)
        sys.modules.pop("apscheduler.schedulers.blocking", None)
        sys.modules.pop("apscheduler.triggers", None)
        sys.modules.pop("apscheduler.triggers.cron", None)
        sys.modules.pop("apscheduler.triggers.date", None)


def test_format_job_next_run_handles_missing_attribute():
    scheduler_service = load_scheduler_service_module()

    class JobWithoutNextRun:
        pass

    assert scheduler_service.format_job_next_run(JobWithoutNextRun()) == "n/a"


def test_format_job_next_run_formats_datetime_value():
    scheduler_service = load_scheduler_service_module()

    class JobWithNextRun:
        next_run_time = datetime(2026, 4, 1, 10, 30, tzinfo=UTC)

    assert (
        scheduler_service.format_job_next_run(JobWithNextRun())
        == "2026-04-01 10:30:00 UTC"
    )
