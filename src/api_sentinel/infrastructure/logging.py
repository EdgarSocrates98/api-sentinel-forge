"""Structured JSON-lines logging — secret-free by construction."""

from __future__ import annotations

import json
import logging
from typing import Any

from api_sentinel.infrastructure.security.redact import redact


class _RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()).text,
        }
        for key, value in record.__dict__.items():
            if key.startswith("sentinel_"):
                payload[key.removeprefix("sentinel_")] = value
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info)).text
        return json.dumps(payload, default=str)


def get_logger(name: str, *, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_RedactingJsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
    return logger


def bind(logger: logging.Logger, **context: str) -> logging.LoggerAdapter[logging.Logger]:
    """Attach contextual fields (emitted as flat JSON keys)."""
    return logging.LoggerAdapter(logger, {f"sentinel_{k}": v for k, v in context.items()})
