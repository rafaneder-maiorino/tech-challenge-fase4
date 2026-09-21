"""Validate a delivered batch, quarantine what fails, stop the pipeline if needed.

This is the stage-1 gate. It imports the two contracts and the severity mapping
of stage 1's first half and adds no rules of its own — a rule that lived here
would be a rule no reviewer would find when reading the contracts.

What it does, in order:

1. Verifies up front that every check the schema registers has a severity. The
   batch is not touched until this passes.
2. Validates with ``lazy=True``, so one run reports every failure rather than
   the first. An operator who has to rerun a batch six times to learn six
   things has a worse pipeline than one that says all six at once.
3. Resolves each failure back to its rule and looks up the severity.
4. Quarantines the rows that failed a blocker, with a JSONL beside them saying
   why, so a human can act without rerunning anything.
5. Writes the JSON and HTML reports, and exits non-zero if any blocker fired.

**On the strict severity lookup.** ``severity_for`` in the contracts package
fails closed — an unrecognised name becomes a blocker — which is the right
answer for a consumer that must keep running, such as an alerting sink. It is
the wrong answer here. Stage 1's first half turned up a pandera trap where
inherited checks silently reported under a different name than the one they
were registered with; had the pipeline been defaulting, that would have
surfaced as every warning quietly becoming a blocker and nobody noticing which
rule had actually fired. So this module refuses to guess: an unmapped name
raises :class:`UnknownRuleError`. The preflight in step 1 is what makes that
safe — the mismatch is found before any data is read, not halfway through a
batch.

Run as ``python -m credit_monitor.pipeline.ingest <path-to-parquet>``.
"""

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pandera.pandas as pa

from credit_monitor.constants import (
    QUARANTINE_DIR,
    VALIDATION_REPORTS_DIR,
)
from credit_monitor.contracts import RawCreditSchema
from credit_monitor.contracts.failures import (
    assert_rules_are_classified,
    resolve_rule,
)
from credit_monitor.contracts.severity import RULE_SEVERITY, Severity
from credit_monitor.logging_config import configure_json_logging
from credit_monitor.reporting.validation_report import (
    Rejection,
    RuleCount,
    Scope,
    ValidationResult,
    utc_timestamp,
    write_html_report,
    write_json_report,
)

log = logging.getLogger(__name__)

EXIT_OK: int = 0
EXIT_BLOCKED: int = 1


def _python_scalar(value: object) -> object:
    """Convert a pandas/numpy scalar to something json.dumps can take."""
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        # pd.isna raises on anything array-like; such a value is not a scalar
        # failure case and is better rendered than dropped.
        return str(value)
    return value.item() if hasattr(value, "item") else value


def _rejections_from(failure_cases: pd.DataFrame) -> list[Rejection]:
    """Turn pandera's failure table into one record per (rule, row).

    Deduplicating is the point. Pandera melts a frame-level check across every
    column, so one duplicated row arrives as eleven failure cases naming eleven
    columns; kept raw, the report would claim eleven rejections for one row and
    the injected-defect manifest would never line up. For those checks the row
    itself is the evidence — it is in the quarantine parquet in full — so the
    column and the observed value are dropped rather than picked arbitrarily.
    Column-level checks keep both, since there the cell *is* the finding.
    """
    seen: dict[tuple[str, int | None, str | None], Rejection] = {}
    for case in failure_cases.itertuples():
        rule = resolve_rule(str(case.check))
        severity = RULE_SEVERITY[rule]
        row_index = _python_scalar(case.index)
        scope = Scope.BATCH if row_index is None else Scope.ROW

        frame_level = case.schema_context == "DataFrameSchema"
        column = None if frame_level else _python_scalar(case.column)
        observed = None if frame_level else _python_scalar(case.failure_case)

        key = (rule.value, row_index, column)
        if key not in seen:
            seen[key] = Rejection(
                row_index=row_index,
                rule=rule.value,
                column=column,
                observed_value=observed,
                severity=severity,
                scope=scope,
            )
    return list(seen.values())


def _count_rules(rejections: list[Rejection]) -> list[RuleCount]:
    """Aggregate rejections per rule, counting rows rather than failure cases.

    A rule that fails on both scopes at once — a null in a non-nullable column
    (row) alongside a wrong dtype (batch), both of which are
    ``schema_columns_and_dtypes`` — is reported as row-scoped, with the two
    kinds of finding summed. The severity is the same either way, so the
    verdict never depends on this choice.
    """
    rows: dict[str, set[int]] = defaultdict(set)
    batch: dict[str, int] = defaultdict(int)
    severities: dict[str, Severity] = {}

    for rejection in rejections:
        severities[rejection.rule] = rejection.severity
        if rejection.row_index is None:
            batch[rejection.rule] += 1
        else:
            rows[rejection.rule].add(rejection.row_index)

    counts = [
        RuleCount(
            rule=rule,
            severity=severity,
            scope=Scope.ROW if rows[rule] else Scope.BATCH,
            rows=len(rows[rule]) + batch[rule],
        )
        for rule, severity in severities.items()
    ]
    return sorted(counts, key=lambda count: count.rule)


def validate_batch(
    frame: pd.DataFrame,
    batch_id: str,
    source_path: str = "<in-memory>",
    schema: type[pa.DataFrameModel] = RawCreditSchema,
) -> ValidationResult:
    """Validate one batch and return everything learned about it.

    Pure: reads no files, writes none, and never exits. Quarantining and
    reporting are separate steps so this can be called from a test with a
    hand-built frame.

    Args:
        frame: The batch, indexed positionally (see :func:`load_batch`).
        batch_id: Identifier used for the quarantine directory and reports.
        source_path: Where the batch came from, for the report header.
        schema: Contract to apply. The raw contract by default — this is the
            ingestion boundary, and ``ModelInputSchema`` guards the later one.

    Returns:
        The validation outcome, including its verdict.
    """
    assert_rules_are_classified(schema)
    log.info(
        "ingestion.validation_started",
        extra={
            "batch_id": batch_id,
            "schema": schema.__name__,
            "rows_in": len(frame),
        },
    )

    started = time.perf_counter()
    try:
        schema.validate(frame, lazy=True)
        failure_cases = None
    except pa.errors.SchemaErrors as errors:
        failure_cases = errors.failure_cases
    duration = time.perf_counter() - started

    rejections = [] if failure_cases is None else _rejections_from(failure_cases)
    rule_counts = _count_rules(rejections)
    quarantined = {
        rejection.row_index
        for rejection in rejections
        if rejection.severity is Severity.BLOCKER and rejection.row_index is not None
    }

    for count in rule_counts:
        log.warning(
            "contract.rule_violated",
            extra={
                "batch_id": batch_id,
                "rule": count.rule,
                "severity": count.severity.value,
                "scope": count.scope.value,
                "rows": count.rows,
            },
        )

    return ValidationResult(
        batch_id=batch_id,
        source_path=source_path,
        schema_name=schema.__name__,
        timestamp=utc_timestamp(),
        rows_in=len(frame),
        rows_quarantined=len(quarantined),
        duration_seconds=duration,
        rule_counts=rule_counts,
        rejections=rejections,
    )


def quarantine_rows(
    frame: pd.DataFrame,
    result: ValidationResult,
    directory: Path = QUARANTINE_DIR,
) -> Path | None:
    """Write the blocked rows and the reasons for blocking them.

    Two files under ``<directory>/<batch_id>/``: ``rows.parquet`` with the
    offending rows exactly as delivered, and ``rejections.jsonl`` with one
    record per blocking rejection. The parquet is skipped when a blocker was
    batch-scoped only — a wrong dtype indicts the column, not any row — but the
    JSONL is still written, because otherwise the quarantine would be empty and
    a human would have no record of why the batch died.

    Args:
        frame: The batch as ingested.
        result: Its validation outcome.
        directory: Root quarantine directory.

    Returns:
        The directory written, or ``None`` when nothing blocked.
    """
    blocking = [
        rejection
        for rejection in result.rejections
        if rejection.severity is Severity.BLOCKER
    ]
    if not blocking:
        return None

    target = directory / result.batch_id
    target.mkdir(parents=True, exist_ok=True)

    indices = sorted(
        {
            rejection.row_index
            for rejection in blocking
            if rejection.row_index is not None
        }
    )
    rows_path = target / "rows.parquet"
    if indices:
        # The index is kept, and named, because it is the join key back to the
        # JSONL: without it the parquet is twelve anonymous rows and nobody can
        # tell which rule sent which one here.
        rejected = frame.iloc[indices].rename_axis("batch_row_index")
        rejected.to_parquet(rows_path, index=True)

    jsonl_path = target / "rejections.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for rejection in blocking:
            handle.write(
                json.dumps(
                    {
                        "batch_id": result.batch_id,
                        "row_index": rejection.row_index,
                        "rule": rejection.rule,
                        "column": rejection.column,
                        "observed_value": rejection.observed_value,
                        "severity": rejection.severity.value,
                        "scope": rejection.scope.value,
                    },
                    default=str,
                )
                + "\n"
            )

    log.error(
        "ingestion.quarantined",
        extra={
            "batch_id": result.batch_id,
            "rows": len(indices),
            "rejections": len(blocking),
            "rows_path": str(rows_path) if indices else None,
            "jsonl_path": str(jsonl_path),
        },
    )
    return target


def load_batch(path: Path) -> pd.DataFrame:
    """Read a batch, re-indexed positionally.

    ``reset_index`` is not cosmetic: pandera reports failures by index *label*,
    and the quarantine addresses rows by position. Making the two the same on
    the way in is what keeps a rejection record pointing at the row it
    describes.
    """
    return pd.read_parquet(path).reset_index(drop=True)


def ingest(
    path: Path,
    quarantine_dir: Path = QUARANTINE_DIR,
    report_dir: Path = VALIDATION_REPORTS_DIR,
    schema: type[pa.DataFrameModel] = RawCreditSchema,
) -> ValidationResult:
    """Validate a batch file end to end: quarantine, report, return the verdict.

    Args:
        path: Parquet file holding the batch.
        quarantine_dir: Root directory for quarantined rows.
        report_dir: Directory for the JSON and HTML reports.
        schema: Contract to apply.

    Returns:
        The validation outcome. The caller decides what to do about it.
    """
    batch_id = path.stem
    started = time.perf_counter()
    log.info(
        "ingestion.started",
        extra={"batch_id": batch_id, "source_path": str(path)},
    )

    frame = load_batch(path)
    result = validate_batch(frame, batch_id, str(path), schema)

    quarantine_rows(frame, result, quarantine_dir)
    json_path = write_json_report(result, report_dir)
    html_path = write_html_report(result, report_dir)
    log.info(
        "ingestion.report_written",
        extra={
            "batch_id": batch_id,
            "json_path": str(json_path),
            "html_path": str(html_path),
        },
    )

    log.info(
        "ingestion.finished",
        extra={
            "batch_id": batch_id,
            "verdict": result.verdict.value,
            "rows_in": result.rows_in,
            "rows_passed": result.rows_passed,
            "rows_quarantined": result.rows_quarantined,
            "rules_violated": len(result.rule_counts),
            "duration_seconds": round(time.perf_counter() - started, 4),
            "exit_code": EXIT_BLOCKED if result.blocked else EXIT_OK,
        },
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface."""
    parser = argparse.ArgumentParser(
        prog="python -m credit_monitor.pipeline.ingest",
        description=(
            "Valida um lote contra o contrato de dados, põe em quarentena as "
            "linhas que violam uma regra bloqueante e sai com código não-zero "
            "se o pipeline deve parar."
        ),
    )
    parser.add_argument("batch", type=Path, help="parquet do lote a validar")
    parser.add_argument(
        "--quarantine-dir",
        type=Path,
        default=QUARANTINE_DIR,
        help="diretório raiz da quarentena",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=VALIDATION_REPORTS_DIR,
        help="diretório dos relatórios de validação",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the gate and return the process exit code.

    Non-zero on BLOCKED and nothing else: a batch that only tripped warnings
    has been accepted, and failing the pipeline for it would teach everyone to
    ignore the exit code.
    """
    configure_json_logging()
    args = build_parser().parse_args(argv)
    result = ingest(args.batch, args.quarantine_dir, args.report_dir)
    return EXIT_BLOCKED if result.blocked else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
