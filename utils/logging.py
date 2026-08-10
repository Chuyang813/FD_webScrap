"""Minimal structured JSON-line logging."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

_STANDARD_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__)


class JsonFormatter(logging.Formatter):
    """Format each record as a compact, machine-readable JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _STANDARD_RECORD_FIELDS and key not in {"message", "asctime"}
            }
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def get_json_logger(
    name: str = "frontier_dental",
    *,
    level: int | str = logging.INFO,
    stream: TextIO | None = None,
    log_path: str | Path | None = None,
) -> logging.Logger:
    """Return a configured logger without adding duplicate handlers.

    When ``log_path`` is given the JSON stream is written to that file instead of
    stderr. The interactive progress view uses this so the human and machine
    views never compete for the same stream.
    """

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    for existing in [h for h in logger.handlers if getattr(h, "_frontier_json", False)]:
        logger.removeHandler(existing)
        existing.close()
    if log_path is not None:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(path, encoding="utf-8")
    else:
        handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler._frontier_json = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    return logger


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit an INFO event with arbitrary structured fields."""

    logger.info(event, extra={"event": event, **fields})
