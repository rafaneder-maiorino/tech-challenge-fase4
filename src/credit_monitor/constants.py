"""Project-wide constants: dataset identity, filesystem layout and checksums.

Everything that must stay identical across machines and runs lives here, so a
reviewer can read the reproducibility contract of the project in a single file.
"""

from pathlib import Path

# --------------------------------------------------------------------------
# Filesystem layout
#
# Resolved from this file rather than from the current working directory, so
# `make download` and an ad-hoc `python -c ...` land in the same places.
# constants.py -> credit_monitor -> src -> <project root>
# --------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
DATA_DIR: Path = PROJECT_ROOT / "data"
RAW_DATA_DIR: Path = DATA_DIR / "raw"
REPORTS_DIR: Path = PROJECT_ROOT / "reports"

# Stage 1 outputs. All three are generated and gitignored: a quarantine that
# had to be committed would mean the pipeline lost the ability to reproduce it.
DIRTY_DATA_DIR: Path = DATA_DIR / "dirty"
CLEAN_DATA_DIR: Path = DATA_DIR / "clean"
# Reference (treino + linha de base do drift) e holdout (controle do teste A/A).
PROCESSED_DATA_DIR: Path = DATA_DIR / "processed"
# Lotes mensais simulados da etapa 2: features, predições e rótulos
# em arquivos separados, porque em produção eles chegam em momentos
# diferentes.
PRODUCTION_DATA_DIR: Path = DATA_DIR / "production"
SIMULATION_REPORTS_DIR: Path = REPORTS_DIR / "simulation"

# --------------------------------------------------------------------------
# MLflow
#
# SQLite, not the filesystem store: the model registry — and therefore the
# `champion` alias the serving side resolves — is unavailable on the file
# backend, which MLflow 3 also reports as being in maintenance mode.
#
# The UI is `mlflow ui --backend-store-uri sqlite:///mlflow.db --port 5001`.
# Port 5001 because on macOS 5000 is bound by the AirPlay Receiver, which
# answers requests instead of failing, so MLflow appears to start and then
# serves someone else's 403.
# --------------------------------------------------------------------------
MLFLOW_DB_PATH: Path = PROJECT_ROOT / "mlflow.db"
MLFLOW_TRACKING_URI: str = f"sqlite:///{MLFLOW_DB_PATH}"
MLFLOW_ARTIFACT_ROOT: Path = PROJECT_ROOT / "mlruns"
MLFLOW_UI_PORT: int = 5001
MLFLOW_EXPERIMENT: str = "credit-monitor-baseline"
REGISTERED_MODEL_NAME: str = "credit-default-baseline"
CHAMPION_ALIAS: str = "champion"
QUARANTINE_DIR: Path = DATA_DIR / "quarantine"
VALIDATION_REPORTS_DIR: Path = REPORTS_DIR / "validation"

# --------------------------------------------------------------------------
# Source dataset
#
# OpenML id 45577 is "Give-Me-Some-Credit": binary credit-default classification,
# 150k rows, which clears the 5,000-sample floor the challenge sets.
# --------------------------------------------------------------------------
OPENML_DATASET_ID: int = 45577
DATASET_NAME: str = "Give-Me-Some-Credit"
RAW_DATA_PATH: Path = RAW_DATA_DIR / "gmsc.parquet"

# Binary target: 1 = serious delinquency (90+ days past due) within two years.
TARGET_COLUMN: str = "SeriousDlqin2yrs"

# The three delinquency counters. OpenML serves them as uint8 and they are the
# columns where sentinel-looking values (96, 98) show up, so they are named once
# here and reused by the inspection report and, later, by the data contract.
DELINQUENCY_COLUMNS: tuple[str, ...] = (
    "NumberOfTime30-59DaysPastDueNotWorse",
    "NumberOfTimes90DaysLate",
    "NumberOfTime60-89DaysPastDueNotWorse",
)

# --------------------------------------------------------------------------
# Integrity
#
# SHA-256 of the parquet written by src/credit_monitor/data/download.py. It is
# the checksum of OUR file, not of the OpenML payload: parquet encoding is not
# byte-reproducible across pyarrow versions, so this pins the artefact this
# project actually reads.
#
# Empty on a fresh checkout that has never downloaded. The first run of
# `make download` computes it and rewrites the assignment below in place; every
# later run verifies against it and skips the download when it matches. The
# parentheses are not decorative: a 64-character digest on one line would be 94
# columns wide and trip the E501 lint rule.
# --------------------------------------------------------------------------
EXPECTED_RAW_SHA256: str = (
    "eaa601852b10d5c1d6d863de279055b1d59af87ed1246f5e4df865adf8be04f5"
)
