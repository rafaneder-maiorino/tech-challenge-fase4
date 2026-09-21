"""Tests for the reference/holdout split and the seal on the holdout."""

import json

import pandas as pd
import pytest
from conftest import make_random_frame

from credit_monitor.constants import TARGET_COLUMN
from credit_monitor.data.preprocess import (
    INCOME_MISSING_COLUMN,
    clean,
    fit_imputation,
    prepare_split,
)
from credit_monitor.data.split import (
    DEFAULT_SEED,
    HOLDOUT_NAME,
    REFERENCE_NAME,
    build_metadata,
    load_split,
    persist,
    stratified_split,
)

ROWS = 900
POSITIVES = 60  # 6.67%, the rate inspection §2 measured


def cleaned_frame() -> pd.DataFrame:
    frame, _ = clean(make_random_frame(n_rows=ROWS, n_positive=POSITIVES))
    return frame


def test_split_is_exhaustive_and_disjoint() -> None:
    # Nothing invented, nothing lost, nothing in both. A row in both sets
    # would put training data inside the day-8 control group.
    frame = cleaned_frame()

    reference, holdout = stratified_split(frame)

    assert len(reference) + len(holdout) == len(frame)

    # Compared as multisets of rows rather than as frames: the split shuffles,
    # so row order carries no information and only membership does.
    rejoined = (
        pd.concat([reference, holdout])
        .sort_values(by=list(frame.columns))
        .reset_index(drop=True)
    )
    original = frame.sort_values(by=list(frame.columns)).reset_index(drop=True)
    pd.testing.assert_frame_equal(rejoined, original)


def test_split_is_stratified_on_the_target() -> None:
    # At 6.68% positives a uniform split leaves visibly different base rates,
    # and the stage-2 drift monitor would report a difference the splitter
    # created rather than one the population has.
    frame = cleaned_frame()

    reference, holdout = stratified_split(frame)

    rate = lambda f: (f[TARGET_COLUMN] == "1").mean()  # noqa: E731
    assert rate(reference) == pytest.approx(rate(frame), abs=0.005)
    assert rate(holdout) == pytest.approx(rate(frame), abs=0.005)


def test_holdout_fraction_is_honoured() -> None:
    frame = cleaned_frame()

    _, holdout = stratified_split(frame, holdout_fraction=0.30)

    assert len(holdout) == pytest.approx(0.30 * len(frame), abs=1)


def test_same_seed_gives_the_same_split() -> None:
    # The reference set is the drift baseline of stage 2. A baseline that
    # moved between runs would make every drift number unreproducible.
    frame = cleaned_frame()

    first, _ = stratified_split(frame, seed=DEFAULT_SEED)
    second, _ = stratified_split(frame, seed=DEFAULT_SEED)

    pd.testing.assert_frame_equal(first, second)


def test_a_different_seed_gives_a_different_split() -> None:
    frame = cleaned_frame()

    first, _ = stratified_split(frame, seed=1)
    second, _ = stratified_split(frame, seed=2)

    assert not first.equals(second)


def test_persisted_split_round_trips_with_its_metadata(tmp_path) -> None:  # noqa: ANN001
    frame = cleaned_frame()
    reference_raw, holdout_raw = stratified_split(frame)
    fit = fit_imputation(reference_raw)
    reference = prepare_split(REFERENCE_NAME, reference_raw, fit)
    holdout = prepare_split(HOLDOUT_NAME, holdout_raw, fit)

    metadata = build_metadata(
        frame=frame,
        reference=reference.frame,
        holdout=holdout.frame,
        seed=DEFAULT_SEED,
        holdout_fraction=0.30,
        source_sha256="0" * 64,
        created_at="2026-09-20T00:00:00+00:00",
    )
    persist(reference.frame, holdout.frame, metadata, tmp_path)

    # The seed and the row counts are what make a split auditable after the
    # fact; without them the two parquets are just two files.
    written = json.loads((tmp_path / "split_metadata.json").read_text())
    assert written["seed"] == DEFAULT_SEED
    assert written["rows_reference"] == reference.rows
    assert written["rows_holdout"] == holdout.rows
    assert written["stratified_on"] == TARGET_COLUMN
    assert written["rows_reference"] + written["rows_holdout"] == written["rows_total"]

    pd.testing.assert_frame_equal(load_split(REFERENCE_NAME, tmp_path), reference.frame)
    pd.testing.assert_frame_equal(load_split(HOLDOUT_NAME, tmp_path), holdout.frame)


def test_both_splits_satisfy_the_model_contract(tmp_path) -> None:  # noqa: ANN001
    # prepare_split calls assert_model_ready, so reaching this line at all is
    # the assertion; the explicit checks below are what it guarantees.
    frame = cleaned_frame()
    reference_raw, holdout_raw = stratified_split(frame)
    fit = fit_imputation(reference_raw)

    reference = prepare_split(REFERENCE_NAME, reference_raw, fit)
    holdout = prepare_split(HOLDOUT_NAME, holdout_raw, fit)

    for split in (reference, holdout):
        assert split.frame.notna().all().all()
        assert (split.frame["MonthlyIncome"] > 0).all()
        assert 0.05 <= split.positive_rate <= 0.09


def test_imputation_constants_come_from_the_reference_only() -> None:
    # The holdout is the control group of the A/A test. If its income values
    # fed the median the reference is filled with, the two sets would share a
    # statistic and the "no drift" comparison would start out favoured.
    # The nulls go into the RAW frame, so `clean` is what sets the indicator.
    # Nulling income on an already-cleaned frame would leave income_missing at
    # 0 beside a missing value, which is a state the pipeline rejects.
    raw = make_random_frame(n_rows=ROWS, n_positive=POSITIVES)
    raw.loc[0:49, "MonthlyIncome"] = None
    raw.loc[0:49, "NumberOfDependents"] = None
    frame, _ = clean(raw)
    reference_raw, _ = stratified_split(frame)

    fit = fit_imputation(reference_raw)
    whole_frame_fit = fit_imputation(frame)

    present = reference_raw[reference_raw[INCOME_MISSING_COLUMN] == 0]
    assert fit.fitted_on_rows == len(present)
    assert fit.fitted_on_rows < whole_frame_fit.fitted_on_rows
    assert fit.monthly_income_median == pytest.approx(present["MonthlyIncome"].median())
