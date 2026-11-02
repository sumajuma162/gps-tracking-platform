"""One-line JSON logs that include structured ``extra={...}`` fields (they were silently dropped before)."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
               "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        out.update({k: v for k, v in vars(record).items() if k not in _STANDARD and not k.startswith("_")})
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
