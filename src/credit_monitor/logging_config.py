"""Single place where logging is configured.

Every entry point calls :func:`configure_logging` or
:func:`configure_json_logging` before doing any work, so log lines from the
library and from the scripts share one format. Stage 3 of the challenge
consolidates these lines into the observability layer, which is why the format
is fixed here instead of per-module.

Two formats, one decision behind them. The human format is for a terminal a
person is watching. The JSON format is for the ingestion pipeline, whose lines
are events a machine will aggregate — a rule that fired, a count, a duration —
and where a regex over a free-text line would be the thing that breaks first
when a message is reworded.
"""

import datetime as dt
import json
import logging
import sys
from typing import Any

# Fields the observability stage will parse: timestamp, level, logger name and
# message. Kept in one constant so a change never drifts between handlers.
LOG_FORMAT: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT: str = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the root logger to write structured lines to stderr.

    Safe to call more than once: the root handlers are replaced rather than
    appended to, so a script that imports a module which also configures
    logging does not end up with duplicated lines.

    Args:
        level: Minimum level emitted by the root logger.
    """
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


# Attributes LogRecord always carries. Anything outside this set was attached
# by the caller through `extra=` and is therefore part of the event.
_RESERVED_RECORD_FIELDS: frozenset[str] = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Render a record as one JSON object per line.

    Everything passed through ``extra=`` becomes a top-level key, so a consumer
    reads ``record["rule"]`` instead of parsing it back out of a sentence. The
    four fixed keys are the ones every line has regardless of the caller.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialise one record to a single-line JSON object."""
        payload: dict[str, Any] = {
            "timestamp": dt.datetime.fromtimestamp(
                record.created, tz=dt.UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _RESERVED_RECORD_FIELDS
            }
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # default=str so a Path, a Decimal or an Enum in `extra=` degrades to
        # its string form instead of killing the log line that reports a failure.
        return json.dumps(payload, default=str)


def configure_json_logging(level: int = logging.INFO) -> None:
    """Configure the root logger to write one JSON object per line to stderr.

    Same replace-don't-append behaviour as :func:`configure_logging`, so the
    two can be called in any order without duplicating output.

    Args:
        level: Minimum level emitted by the root logger.
    """
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
