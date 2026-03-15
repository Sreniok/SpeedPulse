"""Shared logging configuration for all SpeedPulse scripts.

Usage::

    from logger_setup import get_logger

    logger = get_logger("CheckSpeed")
    logger.info("Running speed test...")

Behaviour:
- Logs to **stderr** so script stdout stays clean for data/piping.
- Default level is ``INFO``; override with the ``LOG_LEVEL`` env var
  (DEBUG, INFO, WARNING, ERROR, CRITICAL).
- Set ``LOG_FORMAT=json`` to emit one JSON object per line (useful for
  Docker / log aggregators).  Default is a human-readable format.
- The ``flush=True`` behaviour of the old ``print()`` calls is preserved
  by using ``StreamHandler`` with an auto-flushing stream wrapper.
"""

from __future__ import annotations

import json as _json
import logging
import os
import sys
from datetime import datetime, timezone


class _AutoFlushStream:
    """Thin wrapper that flushes after every write — mirrors ``print(flush=True)``."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, data):
        self._stream.write(data)
        self._stream.flush()

    def flush(self):
        self._stream.flush()

    # Delegate everything else transparently.
    def __getattr__(self, name):
        return getattr(self._stream, name)


class _JsonFormatter(logging.Formatter):
    """Emit each record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            obj["exception"] = self.formatException(record.exc_info)
        return _json.dumps(obj, ensure_ascii=False)


_HUMAN_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_HUMAN_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Keep a registry so repeated calls for the same name reuse the logger.
_configured: set[str] = set()


def get_logger(name: str) -> logging.Logger:
    """Return a named logger configured according to env vars.

    Safe to call multiple times — handlers are only attached once per *name*.
    """
    logger = logging.getLogger(name)

    if name in _configured:
        return logger

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    handler = logging.StreamHandler(_AutoFlushStream(sys.stderr))
    handler.setLevel(level)

    if os.getenv("LOG_FORMAT", "").lower() == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_HUMAN_FORMAT, datefmt=_HUMAN_DATEFMT))

    logger.addHandler(handler)
    # Prevent duplicate output if the root logger also has handlers.
    logger.propagate = False
    _configured.add(name)
    return logger
