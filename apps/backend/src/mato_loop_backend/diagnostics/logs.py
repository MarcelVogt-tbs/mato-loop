from __future__ import annotations

import json
import logging
import traceback
from collections import deque
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from mato_loop_backend.config import get_runtime_config

MAX_LOG_BYTES = 1024 * 1024
MAX_REPLY_CHARACTERS = 50_000


class BackendLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage()[:8_000],
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exceptionType"] = record.exc_info[0].__name__
            payload["frames"] = [
                {"file": frame.filename, "function": frame.name, "line": frame.lineno}
                for frame in traceback.extract_tb(record.exc_info[2])[-12:]
            ]
        # ASCII JSON keeps byte-based rotation accurate and each record on one line.
        return json.dumps(payload, ensure_ascii=True)


def install_backend_log_handler() -> RotatingFileHandler:
    config = get_runtime_config()
    path = config.data_dir / "logs" / "backend.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=MAX_LOG_BYTES, backupCount=1, encoding="utf-8")
    handler.setFormatter(BackendLogFormatter())
    logger = logging.getLogger("mato_loop_backend")
    logger.setLevel(config.log_level)
    logger.addHandler(handler)
    return handler


def read_backend_logs(
    limit: int, logger_name: str | None = None, level: str | None = None
) -> dict:
    path = get_runtime_config().data_dir / "logs" / "backend.log"
    records: deque[dict] = deque(maxlen=limit)
    minimum_level = logging.getLevelNamesMapping()[level] if level else 0
    for file in (path.with_suffix(".log.1"), path):
        try:
            with file.open("rb") as stream:
                content = stream.read(MAX_LOG_BYTES)
        except FileNotFoundError:
            continue
        for line in content.splitlines():
            try:
                record = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue  # A concurrent write may leave the last record incomplete.
            if logger_name and not (
                record["logger"] == logger_name or record["logger"].startswith(logger_name + ".")
            ):
                continue
            if logging.getLevelNamesMapping().get(record["level"], 0) < minimum_level:
                continue
            records.append(record)
    items = []
    size = len(json.dumps({"items": [], "truncated": False}))
    truncated = False
    for record in reversed(records):
        record_size = len(json.dumps(record)) + (2 if items else 0)
        if size + record_size > MAX_REPLY_CHARACTERS:
            truncated = True
            break
        items.append(record)
        size += record_size
    items.reverse()
    return {"items": items, "truncated": truncated}
