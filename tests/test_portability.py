r"""What a report contains must not depend on the OS that wrote it.

The Windows run found two ways it did. Python's text mode writes ``\r\n``
there, so eight tracked files came out modified after ``make all``; and
``str(path)`` wrote ``data\raw\gmsc.parquet`` into ``reports/inspection.md``.
Both were fixed at every call site, not only at the eight that surfaced, and
these tests keep the next call site honest: they read the source, because the
bug only shows on a machine CI does not run.
"""

import ast
import json
import logging
from pathlib import Path

from credit_monitor.constants import PROJECT_ROOT, RAW_DATA_PATH
from credit_monitor.paths import DisplayPathFilter, display_path, json_default

SOURCES = sorted(
    [*PROJECT_ROOT.glob("scripts/*.py"), *PROJECT_ROOT.glob("src/**/*.py")]
)


def _calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _keywords(call: ast.Call) -> set[str]:
    return {keyword.arg for keyword in call.keywords if keyword.arg}


def _text_write_mode(call: ast.Call) -> bool:
    """``open(..., "w")`` or ``path.open("w")``, not a binary or read mode."""
    candidates = [*call.args, *(k.value for k in call.keywords if k.arg == "mode")]
    for node in candidates:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            mode = node.value
            if set(mode) & set("wax") and "b" not in mode:
                return True
    return False


def test_every_text_write_fixes_its_line_ending() -> None:
    missing = [
        f"{display_path(source)}:{call.lineno} {_name(call)}()"
        for source in SOURCES
        for call in _calls(source)
        if (
            _name(call) == "write_text"
            or (_name(call) == "open" and _text_write_mode(call))
        )
        and "newline" not in _keywords(call)
    ]
    assert not missing, 'sem newline="\\n" — no Windows sai CRLF: ' + ", ".join(missing)


def test_no_stock_file_handler() -> None:
    # logging.FileHandler opens in text mode with no newline=; LfFileHandler
    # in credit_monitor.monitoring.logs is the one to use.
    offenders = [
        f"{display_path(source)}:{call.lineno}"
        for source in SOURCES
        for call in _calls(source)
        if _name(call) == "FileHandler"
    ]
    assert not offenders, "use LfFileHandler: " + ", ".join(offenders)


def test_the_scan_actually_finds_the_writes() -> None:
    # Guard on the guard: a scan that matched nothing would pass forever.
    writes = [
        call
        for source in SOURCES
        for call in _calls(source)
        if _name(call) == "write_text"
    ]
    assert len(writes) >= 17


def test_a_repository_path_is_relative_with_forward_slashes() -> None:
    assert display_path(RAW_DATA_PATH) == "data/raw/gmsc.parquet"


def test_a_path_outside_the_repository_keeps_its_absolute_form(
    tmp_path: Path,
) -> None:
    shown = display_path(tmp_path / "x.parquet")
    assert shown.endswith("/x.parquet")
    assert "\\" not in shown


def test_json_default_writes_paths_like_display_path() -> None:
    payload = json.dumps({"source": RAW_DATA_PATH}, default=json_default)
    assert json.loads(payload)["source"] == "data/raw/gmsc.parquet"


def test_log_arguments_that_are_paths_are_rewritten() -> None:
    record = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "Lendo %s", (RAW_DATA_PATH,), None
    )
    DisplayPathFilter().filter(record)
    assert record.getMessage() == "Lendo data/raw/gmsc.parquet"
