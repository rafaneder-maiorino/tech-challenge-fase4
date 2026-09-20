"""Smoke test for the reproducibility contract of the raw dataset."""

import re

from credit_monitor import constants

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def test_expected_raw_sha256_is_a_full_hex_digest() -> None:
    # A populated constant is what lets `make download` skip the network and
    # what proves the committed pipeline was built on a known artefact. An
    # empty string here means the checksum was never recorded (or was lost in
    # a merge), so every downstream reference distribution is unverifiable.
    checksum = constants.EXPECTED_RAW_SHA256

    assert isinstance(checksum, str)
    assert len(checksum) == 64, f"expected 64 hex chars, got {len(checksum)}"
    assert _SHA256_HEX.match(checksum), f"not a lowercase hex digest: {checksum!r}"
