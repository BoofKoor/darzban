"""Regression tests for `XRayCore.get_x25519` output parsing.

The upstream `xray x25519` command renamed its output labels at v25.3.6.
The NEW_FORMAT fixture below is the verbatim label shape emitted by the
pinned v26.5.9 binary (captured from `xray x25519`):

    PrivateKey: <base64>
    Password (PublicKey): <base64>
    Hash32: <hex>

— the public key is the "Password (PublicKey)" line; "Hash32" is
unrelated and ignored. The original regex was pinned to the old
`Private key:` / `Public key:` labels and silently returned `None`
against this format, breaking the REALITY `privateKey`→`publicKey`
derivation path for inbounds that omit `publicKey`. These tests pin the
parser to both shapes (including the `(PublicKey)` parenthetical) so
future bumps don't regress it again.
"""

from unittest.mock import patch

from app.xray.core import XRayCore


OLD_FORMAT = (
    "Private key: aGFpa3UtcHJpdmF0ZS1rZXktYmFzZTY0LWdvZXMtaGVyZQ\n"
    "Public key: aGFpa3UtcHVibGljLWtleS1iYXNlNjQtZ29lcy1oZXJlAA\n"
)

# Verbatim label shape from the pinned v26.5.9 binary — note the
# "(PublicKey)" parenthetical on the Password line.
NEW_FORMAT = (
    "PrivateKey: aGFpa3UtcHJpdmF0ZS1rZXktYmFzZTY0LWdvZXMtaGVyZQ\n"
    "Password (PublicKey): aGFpa3UtcHVibGljLWtleS1iYXNlNjQtZ29lcy1oZXJlAA\n"
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
