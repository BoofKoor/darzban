"""Tests for `PUT/GET /api/core/config` — the pre-apply gate + rollback.

Boundaries that touch a real Xray binary / DB are mocked:
  * `xray.core.test_config` / `xray.core.restart` (no subprocess),
  * `XRayConfig.include_db_users` -> identity (no user DB query),
  * `xray.hosts.update` -> no-op (no host DB query),
  * `XRAY_JSON` -> a tmp file.
What's exercised for real: the endpoint's ordering, status codes, and the
file-level backup/atomic-write/rollback behavior.
"""

import json
import os
from unittest.mock import Mock

import pytest

from app import app as fastapi_app
from app import xray as xray_module
from app.models.admin import Admin
from app.xray.config import XRayConfig
from app.xray.core import XRayConfigError

_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "xray_config.json")
with open(_FIXTURE) as _f:
    VALID_PAYLOAD = json.load(_f)


@pytest.fixture
def sudo_client(client):
    """`client` with the sudo-admin dependency satisfied."""
    fastapi_app.dependency_overrides[Admin.check_sudo_admin] = lambda: object()
    try:
        yield client
    finally:
        fastapi_app.dependency_overrides.pop(Admin.check_sudo_admin, None)


@pytest.fixture(autouse=True)
def _isolate_xray_and_config(tmp_path, monkeypatch):
    """Redirect XRAY_JSON to tmp, neutralize DB-touching helpers, and restore
    any `xray.config` shadow the endpoint installs."""
    cfg_path = tmp_path / "xray_config.json"
    cfg_path.write_text(json.dumps(VALID_PAYLOAD, indent=4))
    monkeypatch.setattr("app.routers.core.XRAY_JSON", str(cfg_path))

    monkeypatch.setattr(XRayConfig, "include_db_users", lambda self: self)
    monkeypatch.setattr(xray_module.hosts, "update", lambda: None)

    yield cfg_path

    # The endpoint assigns `xray.config = ...`, shadowing the lazy __getattr__.
    xray_module.__dict__.pop("config", None)


def _put(client):
    return client.put("/api/core/config", json=VALID_PAYLOAD)


def test_gate_blocks_bad_config(sudo_client, monkeypatch, _isolate_xray_and_config):
    cfg_path = _isolate_xray_and_config
    before = cfg_path.read_text()

    monkeypatch.setattr(xray_module.core, "test_config",
                        Mock(side_effect=XRayConfigError("infra/conf: invalid inbound")))
    restart = Mock()
    monkeypatch.setattr(xray_module.core, "restart", restart)

    resp = _put(sudo_client)

    assert resp.status_code == 422
    assert "invalid inbound" in resp.text
    restart.assert_not_called()
    assert cfg_path.read_text() == before  # nothing written


def test_happy_path_applies_and_writes(sudo_client, monkeypatch, _isolate_xray_and_config):
    cfg_path = _isolate_xray_and_config

    monkeypatch.setattr(xray_module.core, "test_config", Mock())
    restart = Mock()
    monkeypatch.setattr(xray_module.core, "restart", restart)

    resp = _put(sudo_client)

    assert resp.status_code == 200
    restart.assert_called_once()
    assert json.loads(cfg_path.read_text()) == VALID_PAYLOAD


def test_restart_failure_rolls_back(sudo_client, monkeypatch, _isolate_xray_and_config):
    cfg_path = _isolate_xray_and_config
    good = json.loads(cfg_path.read_text())

    # A *different* but valid payload to apply (so a non-rollback would change
    # the file on disk).
    new_payload = json.loads(json.dumps(VALID_PAYLOAD))
    new_payload["inbounds"][0]["port"] = 1081

    monkeypatch.setattr(xray_module.core, "test_config", Mock())
    # First restart (apply) fails; second (rollback to previous) succeeds.
    restart = Mock(side_effect=[RuntimeError("core died"), None])
    monkeypatch.setattr(xray_module.core, "restart", restart)

    resp = sudo_client.put("/api/core/config", json=new_payload)

    assert resp.status_code == 500
    assert "rolled back" in resp.text
    assert restart.call_count == 2  # apply + rollback restart
    assert json.loads(cfg_path.read_text()) == good  # file restored from .bak


def test_get_parses_commented_on_disk_config(sudo_client, _isolate_xray_and_config):
    cfg_path = _isolate_xray_and_config
    cfg_path.write_text(
        '{\n  // operator note\n  "inbounds": [{"tag": "x"}],\n'
        '  "outbounds": [{"tag": "DIRECT"}],\n}\n'  # trailing comma + comment
    )

    resp = sudo_client.get("/api/core/config")

    assert resp.status_code == 200
    assert resp.json() == {"inbounds": [{"tag": "x"}], "outbounds": [{"tag": "DIRECT"}]}
