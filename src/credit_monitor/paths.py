r"""Paths as they appear in content: repository-relative, forward slashes.

A path written into a report, a JSON document or a log line is text a person
reads, and it must not depend on the machine that wrote it. ``str(path)`` does:
on Windows ``reports/inspection.md`` came out reading ``data\raw\gmsc.parquet``,
and a tracked report that changes with the OS can never reproduce byte for
byte. Every path that ends up in content goes through :func:`display_path`.

Paths handed to the operating system — ``open``, ``subprocess``, Docker mounts —
stay native ``Path`` objects. This module is only for the text.
"""

import contextlib
import logging
from pathlib import Path, PurePath

from credit_monitor.constants import PROJECT_ROOT


def display_path(path: str | PurePath) -> str:
    """``path`` relative to the repository when inside it, with ``/`` always.

    Args:
        path: A path, absolute or relative to the working directory.

    Returns:
        ``data/raw/gmsc.parquet`` on every OS; a path outside the repository
        keeps its absolute form, still with forward slashes.
    """
    candidate = Path(path)
    with contextlib.suppress(ValueError):
        candidate = candidate.resolve().relative_to(PROJECT_ROOT)
    return candidate.as_posix()


def json_default(value: object) -> str:
    """``default=`` for ``json.dumps``: paths via :func:`display_path`.

    Anything else degrades to ``str``, as ``default=str`` did before, so a
    Decimal or an Enum still cannot kill the line that reports a failure.
    """
    if isinstance(value, PurePath):
        return display_path(value)
    return str(value)


class DisplayPathFilter(logging.Filter):
    """Rewrite ``Path`` arguments of a log record through :func:`display_path`.

    ``log.info("Lendo %s", source)`` is the common shape, and fixing it here
    covers every such call instead of each one by hand. Paths passed through
    ``extra=`` are handled by the JSON formatter's ``json_default``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the record's path arguments in place; never drop it."""
        if isinstance(record.args, tuple):
            record.args = tuple(
                display_path(arg) if isinstance(arg, PurePath) else arg
                for arg in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: display_path(arg) if isinstance(arg, PurePath) else arg
                for key, arg in record.args.items()
            }
        return True
