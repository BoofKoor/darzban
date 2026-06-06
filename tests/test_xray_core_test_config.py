"""Tests for `XRayCore.test_config` — the pre-apply `xray run -test` gate.

Mirrors the mock-subprocess style of `tests/test_xray_core_mldsa65.py`. The
gate must hard-fail (raise `XRayConfigError`) only on a non-zero return code,
and soft-pass (never raise) when validation can't be performed, so it can only
ever prevent a known-bad apply.
"""

import subprocess
import sys
from unittest.mock import patch

import pytest

from app.xray.core import XRayConfigError, XRayCore

# The real module object. `app.xray.core` as an *attribute* resolves to the
# lazy `_core` instance via the package's PEP-562 __getattr__ (and so does
# `import app.xray.core as ...`), so reach into sys.modules for the module.
core_module = sys.modules["app.xray.core"]


class _StubConfig:
    """Minimal stand-in for XRayConfig — only `to_json()` is used by the gate."""

    def __init__(self, payload='{"inbounds": [], "outbounds": []}'):
        self._payload = payload

    def to_json(self):
        return self._payload


def _core():
    # XRayCore.__init__ is side-effect-free (version is a cached_property).
    return XRayCore("/usr/bin/xray", "/usr/share/xray")


def test_returncode_zero_passes():
    core = _core()
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("app.xray.core.subprocess.run", return_value=completed) as run:
        core.test_config(_StubConfig())  # must not raise
    run.assert_called_once()


def test_nonzero_returncode_raises_with_stderr():
    core = _core()
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="infra/conf: bad inbound settings"
    )
    with patch("app.xray.core.subprocess.run", return_value=completed):
        with pytest.raises(XRayConfigError) as ei:
            core.test_config(_StubConfig())
    assert "bad inbound settings" in ei.value.stderr


def test_missing_binary_soft_passes():
    core = _core()
    with patch("app.xray.core.subprocess.run", side_effect=FileNotFoundError()):
        core.test_config(_StubConfig())  # must not raise


def test_timeout_soft_passes():
    core = _core()
    with patch("app.xray.core.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="xray", timeout=10)):
        core.test_config(_StubConfig())  # must not raise


def test_disabled_skips_subprocess(monkeypatch):
    monkeypatch.setattr(core_module, "XRAY_VALIDATE_BEFORE_APPLY", False)
    core = _core()
    with patch("app.xray.core.subprocess.run") as run:
        core.test_config(_StubConfig())
    run.assert_not_called()


def test_invocation_is_faithful_to_start():
    """cmd shape, env, and stdin must match what start() runs, so a passing
    test faithfully predicts runtime."""
    core = _core()
    cfg = _StubConfig('{"some": "config"}')
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("app.xray.core.subprocess.run", return_value=completed) as run:
        core.test_config(cfg)

    args, kwargs = run.call_args
    assert args[0] == ["/usr/bin/xray", "run", "-test", "-config", "stdin:"]
    assert kwargs["env"] == core._env == {"XRAY_LOCATION_ASSET": "/usr/share/xray"}
    assert kwargs["input"] == '{"some": "config"}'
