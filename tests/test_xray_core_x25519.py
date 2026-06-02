"""Regression tests for `XRayCore.get_x25519` output parsing.

The upstream `xray x25519` command renamed its output labels at v25.3.6
(old: `Private key: …\\nPublic key: …`; new:
`PrivateKey: …\\nPassword: …\\nHash32: …` — where `Password` IS the
public key per upstream). The original regex was pinned to the old
labels and silently returned `None` against the new format, breaking
the REALITY `privateKey`→`publicKey` derivation path for inbounds that
omit `publicKey`. These tests pin the parser to both shapes so future
bumps don't regress it again.
"""

from unittest.mock import patch

from app.xray.core import XRayCore


OLD_FORMAT = (
    "Private key: aGFpa3UtcHJpdmF0ZS1rZXktYmFzZTY0LWdvZXMtaGVyZQ\n"
    "Public key: aGFpa3UtcHVibGljLWtleS1iYXNlNjQtZ29lcy1oZXJlAA\n"
)

NEW_FORMAT = (
    "PrivateKey: aGFpa3UtcHJpdmF0ZS1rZXktYmFzZTY0LWdvZXMtaGVyZQ\n"
    "Password: aGFpa3UtcHVibGljLWtleS1iYXNlNjQtZ29lcy1oZXJlAA\n"
    "Hash32: 0123456789abcdef0123456789abcdef\n"
)


def _run(output: str):
    core = XRayCore.__new__(XRayCore)
    core.executable_path = "/usr/bin/xray"
    with patch("app.xray.core.subprocess.check_output", return_value=output.encode("utf-8")):
        return core.get_x25519()


def test_get_x25519_parses_pre_v25_3_6_labels():
    keys = _run(OLD_FORMAT)
    assert keys == {
        "private_key": "aGFpa3UtcHJpdmF0ZS1rZXktYmFzZTY0LWdvZXMtaGVyZQ",
        "public_key": "aGFpa3UtcHVibGljLWtleS1iYXNlNjQtZ29lcy1oZXJlAA",
    }


def test_get_x25519_parses_v25_3_6_plus_labels():
    keys = _run(NEW_FORMAT)
    assert keys == {
        "private_key": "aGFpa3UtcHJpdmF0ZS1rZXktYmFzZTY0LWdvZXMtaGVyZQ",
        "public_key": "aGFpa3UtcHVibGljLWtleS1iYXNlNjQtZ29lcy1oZXJlAA",
    }
