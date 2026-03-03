"""Structured JSON logging for Aptoflow workflows."""

import json
import logging
import sys
from datetime import datetime, timezone


_LOGRECORD_BUILTIN_KEYS = {
    "name", "msg", "args", "created", "relativeCreated", "thread", "threadName",
    "process", "processName", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "msecs", "message", "taskName",
}


class JSONFormatter(logging.Formatter):
    """Formats log records as JSON lines to stdout."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Include extra fields (workflow, iteration, cost, etc.)
        for key, value in record.__dict__.items():
            if key not in _LOGRECORD_BUILTIN_KEYS:
                log_entry[key] = value
        return json.dumps(log_entry, default=str)


def get_logger(name: str) -> logging.Logger:
    """Create a logger with structured JSON output.

    Args:
        name: Logger name, typically the workflow or module name.

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
