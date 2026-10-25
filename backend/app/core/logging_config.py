"""Logging setup. LOG_FORMAT=json (production) writes one JSON object per line, with the
request ID on every record, so logs can be shipped to any aggregator and joined to a
request; LOG_FORMAT=text is the readable local-development format."""

import json
import logging
from datetime import UTC, datetime

from app.core.middleware import RequestIdLogFilter

# Attributes every LogRecord has; anything else on a record came from `extra=` and is
# written as a field of its own.
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "request_id"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


TEXT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s [%(request_id)s]: %(message)s"


def configure_logging(log_format: str = "text", level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if log_format == "json" else logging.Formatter(TEXT_FORMAT))
    handler.addFilter(RequestIdLogFilter())
    handler._sentinel = True  # so a second call replaces it instead of duplicating output

    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if not getattr(h, "_sentinel", False)] + [handler]
    root.setLevel(level.upper())

    # Uvicorn installs its own handlers; send its records through ours instead. Its access
    # log is replaced by app.access (written by the security middleware), which carries
    # the request ID and latency.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers[:] = []
        uv.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
