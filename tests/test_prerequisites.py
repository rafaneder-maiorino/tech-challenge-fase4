"""The messages a fresh clone sees when it runs things out of order.

`mlruns/`, `mlflow.db` and `data/` are all gitignored, so a clone has none of
them. Every entry point below therefore fails on a clean machine, and what it
prints is the difference between "this project is broken" and "run this first".

These tests pin the message, not the exception type: an MLflow stack trace
ending in "Registered Model with name=... not found" is technically a correct
failure and practically useless.
"""

from pathlib import Path

import pytest

from credit_monitor.models.score import MISSING_CHAMPION_MESSAGE, load_champion
from credit_monitor.simulation.arms import MISSING_REFERENCE_MESSAGE, ensure_scenario


def test_a_missing_champion_names_the_command_that_produces_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point the tracking URI at an empty directory: no registry, no champion.
    monkeypatch.setattr(
        "credit_monitor.models.score.MLFLOW_TRACKING_URI",
        f"sqlite:///{tmp_path / 'empty.db'}",
    )

    with pytest.raises(SystemExit) as failure:
        load_champion()

    message = str(failure.value)
    assert "make train" in message
    assert "make all" in message
    # The URI is named too, so the reader knows which model was looked for.
    assert "credit-default-baseline" in message


def test_a_missing_reference_names_make_prepare(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as failure:
        ensure_scenario(
            "stress_only",
            model=object(),  # type: ignore[arg-type]
            batches_dir=tmp_path / "batches",
            data_dir=tmp_path / "processed",
        )

    message = str(failure.value)
    assert "make prepare" in message
    assert "reference.parquet" in message


def test_the_messages_name_a_command_and_not_only_a_file() -> None:
    # The rule this whole file exists for: a prerequisite error tells the
    # reader what to RUN. A path alone sends them to search the Makefile.
    for template in (MISSING_CHAMPION_MESSAGE, MISSING_REFERENCE_MESSAGE):
        assert "make " in template


def test_an_arm_already_on_disk_is_not_regenerated(tmp_path: Path) -> None:
    # Cheap guard on the lazy path: ensure_scenario must return early when the
    # batches exist, or `make bias` would re-simulate on every run.
    batches = tmp_path / "batches"
    (batches / "ablation_stress_only" / "month_00").mkdir(parents=True)
    (batches / "ablation_stress_only" / "month_00" / "features.parquet").touch()

    directory = ensure_scenario(
        "stress_only",
        model=object(),  # type: ignore[arg-type]
        batches_dir=batches,
        data_dir=tmp_path / "does-not-exist",
    )

    assert directory == batches / "ablation_stress_only"
