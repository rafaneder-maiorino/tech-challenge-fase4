"""Single place where logging is configured.

Every entry point calls :func:`configure_logging` before doing any work, so log
lines from the library and from the scripts share one format. Stage 3 of the
challenge consolidates these lines into the observability layer, which is why
the format is fixed here instead of per-module.
"""

import logging
import sys

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
