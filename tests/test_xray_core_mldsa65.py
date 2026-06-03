"""Regression tests for `XRayCore.get_mldsa65` output parsing.

`xray mldsa65` was added in Xray v26.x as part of the REALITY
post-quantum signature work. Output shape (v26.5.9, captured live):

    Seed:   <base64.RawURLEncoding>
    Verify: <base64.RawURLEncoding, ~2400 chars>

The Verify key feeds the client's `realitySettings.mldsa65Verify`
(v2ray-json) and the `pqv=` share-link param (v2ray base64).
"""

from unittest.mock import patch

from app.xray.core import XRayCore


# Verbatim label shape from the v26.5.9 binary. The Verify key is
# truncated here for readability but the parser must capture every
# non-whitespace character of the value.
V26_5_9_OUTPUT = (
    "Seed: ciUsoRnpqhWPm7IqaRCLNHvNy-J7FB8WAz9-4mqjOcY\n"
    "Verify: B12zgxsQKinnJToEFtJaTdnF5aAyOkSeTAlldTa_fr8wJczyUKxsZmjMTJBZ\n"
)


def _run(output: str):
    core = XRayCore.__new__(XRayCore)
    core.executable_path = "/usr/bin/xray"
    with patch("app.xray.core.subprocess.check_output", return_value=output.encode("utf-8")):
        return core.get_mldsa65(seed="any-seed-stub")


def test_get_mldsa65_parses_v26_5_9_output():
    keys = _run(V26_5_9_OUTPUT)
    assert keys == {
        "seed": "ciUsoRnpqhWPm7IqaRCLNHvNy-J7FB8WAz9-4mqjOcY",
        "verify": "B12zgxsQKinnJToEFtJaTdnF5aAyOkSeTAlldTa_fr8wJczyUKxsZmjMTJBZ",
    }


def test_get_mldsa65_returns_none_when_labels_missing():
    keys = _run("garbage\nno labels here\n")
    assert keys is None
