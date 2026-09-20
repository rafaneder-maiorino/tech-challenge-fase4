"""Download the raw Give-Me-Some-Credit dataset from OpenML.

Raw means raw: the frame is written to parquet exactly as OpenML serves it.
No column is dropped, renamed, imputed, clipped or retyped here. The sentinel
codes, the impossible ages and the missing incomes are all deliberately left in
place — they are the material the data contract of stage 1 has to intercept,
and inspecting a pre-cleaned frame would hide them.

Reproducibility works through a SHA-256 of the written file, kept in
:data:`credit_monitor.constants.EXPECTED_RAW_SHA256`:

* the first run has no expected value, so it computes one, reports it, and
  writes it back into ``constants.py``;
* later runs hash the file on disk and skip the network entirely when it
  matches;
* a mismatch is logged as a warning and the file is re-downloaded, because a
  changed artefact means every downstream reference distribution is stale.

Run as ``python -m credit_monitor.data.download``.
"""

import hashlib
import logging
import re
from pathlib import Path

import pandas as pd

from credit_monitor.constants import (
    DATA_DIR,
    DATASET_NAME,
    EXPECTED_RAW_SHA256,
    OPENML_DATASET_ID,
    RAW_DATA_DIR,
    RAW_DATA_PATH,
)
from credit_monitor.logging_config import configure_logging

log = logging.getLogger(__name__)

# Read in blocks rather than whole: the parquet is small today, but a hashing
# helper that loads the file into memory is the kind of thing that is reused
# later on something that does not fit.
_HASH_CHUNK_BYTES = 1024 * 1024

# The assignment in constants.py rewritten after the first download. Anchored to
# the start of a line so a mention of the name inside a docstring is never
# touched. Both shapes are accepted: the empty `= ""` of a fresh checkout, and
# the parenthesised form written below (a populated digest does not fit on one
# 88-column line).
_CHECKSUM_ASSIGNMENT = re.compile(
    r'^EXPECTED_RAW_SHA256: str = (?:"[0-9a-f]*"|\(\n {4}"[0-9a-f]*"\n\))$',
    flags=re.MULTILINE,
)

_CONSTANTS_PATH = Path(__file__).resolve().parents[1] / "constants.py"


def compute_sha256(path: Path) -> str:
    """Return the lowercase hex SHA-256 of a file, read in chunks.

    Args:
        path: File to hash.

    Returns:
        The 64-character hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_raw_frame() -> pd.DataFrame:
    """Fetch the full dataset from OpenML as a single dataframe.

    The OpenML cache is pointed at ``data/`` so the download leaves nothing
    outside the project tree.

    Returns:
        The dataset exactly as OpenML serves it, target column included.
    """
    import openml

    # Keep OpenML's cache inside the (gitignored) data directory instead of the
    # default ~/.openml, so a clone of this project is self-contained.
    openml.config.set_root_cache_directory(str(DATA_DIR / "openml_cache"))

    log.info("Fetching OpenML dataset id=%d (%s)", OPENML_DATASET_ID, DATASET_NAME)
    dataset = openml.datasets.get_dataset(OPENML_DATASET_ID)

    # target=None returns every column in one frame, which is what "raw" means
    # here: splitting X from y is a modelling decision, not an ingestion one.
    frame, _, _, _ = dataset.get_data(dataset_format="dataframe", target=None)
    log.info("Received frame with shape %s", frame.shape)
    return frame


def persist_expected_checksum(checksum: str) -> None:
    """Write ``checksum`` into the ``EXPECTED_RAW_SHA256`` line of constants.py.

    Only that one line is rewritten; the rest of the file is untouched, so the
    diff a reviewer sees is a single value appearing.

    Args:
        checksum: The 64-character hex digest to record.

    Raises:
        RuntimeError: If the expected assignment line is not found, which means
            constants.py was edited into a shape this function cannot patch.
    """
    source = _CONSTANTS_PATH.read_text(encoding="utf-8")
    replacement = f'EXPECTED_RAW_SHA256: str = (\n    "{checksum}"\n)'
    patched, count = _CHECKSUM_ASSIGNMENT.subn(replacement, source)
    if count != 1:
        raise RuntimeError(
            f"Expected exactly one EXPECTED_RAW_SHA256 assignment in "
            f"{_CONSTANTS_PATH}, found {count}. Restore the line "
            f'`EXPECTED_RAW_SHA256: str = ""` and re-run.'
        )
    _CONSTANTS_PATH.write_text(patched, encoding="utf-8")
    log.info("Recorded expected checksum in %s", _CONSTANTS_PATH)


def download(force: bool = False) -> Path:
    """Ensure the raw parquet exists on disk and matches the expected checksum.

    Args:
        force: Download again even when the local file already matches.

    Returns:
        Path to the raw parquet.
    """
    if RAW_DATA_PATH.exists() and not force:
        actual = compute_sha256(RAW_DATA_PATH)
        if not EXPECTED_RAW_SHA256:
            # A file exists but no checksum was ever recorded — adopt this one
            # rather than spending a download to reproduce a file we already
            # have.
            log.warning(
                "No expected checksum recorded yet; adopting the checksum of "
                "the existing file at %s",
                RAW_DATA_PATH,
            )
            _report_new_checksum(actual)
            return RAW_DATA_PATH
        if actual == EXPECTED_RAW_SHA256:
            log.info(
                "Checksum matches (%s); skipping download of %s",
                actual,
                RAW_DATA_PATH.name,
            )
            return RAW_DATA_PATH
        log.warning(
            "CHECKSUM MISMATCH for %s — expected %s, found %s. "
            "The local file is not the artefact this project was built on; "
            "re-downloading.",
            RAW_DATA_PATH,
            EXPECTED_RAW_SHA256,
            actual,
        )

    frame = fetch_raw_frame()

    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(RAW_DATA_PATH, index=False)
    log.info("Wrote %s (%.1f KiB)", RAW_DATA_PATH, RAW_DATA_PATH.stat().st_size / 1024)

    actual = compute_sha256(RAW_DATA_PATH)
    if not EXPECTED_RAW_SHA256:
        _report_new_checksum(actual)
    elif actual != EXPECTED_RAW_SHA256:
        # Not fatal: parquet encoding shifts between pyarrow versions, so a
        # legitimately identical dataset can hash differently. Loud, because
        # the alternative explanation is that the source data changed.
        log.warning(
            "Freshly downloaded file hashes to %s but constants.py expects %s. "
            "Verify the data before trusting it, then update the constant.",
            actual,
            EXPECTED_RAW_SHA256,
        )
    else:
        log.info("Checksum verified: %s", actual)

    return RAW_DATA_PATH


def _report_new_checksum(checksum: str) -> None:
    """Report a first-run checksum on the terminal and persist it.

    Args:
        checksum: The 64-character hex digest just computed.
    """
    # Logged rather than printed: src/ has no print(), and the observability
    # stage reads these lines. The banner makes it findable in a busy log.
    log.info("=" * 72)
    log.info("FIRST RUN — expected SHA-256 of %s:", RAW_DATA_PATH.name)
    log.info("    %s", checksum)
    log.info("=" * 72)
    persist_expected_checksum(checksum)


def main() -> None:
    """Entry point for ``python -m credit_monitor.data.download``."""
    configure_logging()
    download()


if __name__ == "__main__":
    main()
