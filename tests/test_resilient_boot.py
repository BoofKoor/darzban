"""Tests for resilient Xray-config boot (`app/xray/__init__.py`).

The boot config load must never raise (a raise bricks the first request that
touches `xray.config`). It falls back primary -> `.bak` -> minimal built-in,
and the minimal built-in must stay *controllable* (API inbound injected).
"""

import json
import os

from app import xray as xray_module

_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "xray_config.json")
with open(_FIXTURE) as _f:
    VALID = json.load(_f)


def test_minimal_fallback_is_controllable():
    """`_apply_api` must inject the gRPC API stack so degraded mode can still
    drive Xray (stats, add/remove users), with the placeholder on a port
    distinct from the API port."""
    api_port = 18099
    cfg = xray_module._minimal_fallback_config(api_port=api_port)

    # API control plane injected.
    api_inbound = cfg.get_inbound("API_INBOUND")
    assert api_inbound is not None
    assert api_inbound["port"] == api_port
    assert "HandlerService" in cfg["api"]["services"]
    assert cfg.get("routing", {}).get("rules")  # API routing rule present

    # No-op placeholder keeps it minimal; freedom outbound present.
    placeholder = cfg.get_inbound("FALLBACK_PLACEHOLDER")
    assert placeholder is not None
    assert placeholder["port"] != api_port
    assert any(o.get("protocol") == "freedom" for o in cfg["outbounds"])


def test_resilient_load_uses_backup_when_primary_corrupt(tmp_path, monkeypatch):
    primary = tmp_path / "xray_config.json"
    primary.write_text("{ not valid json :::")
    (tmp_path / "xray_config.json.bak").write_text(json.dumps(VALID))
    monkeypatch.setattr("app.xray.XRAY_JSON", str(primary))

    cfg = xray_module._load_config_resilient(api_port=18099)

    # Loaded from the backup: carries the fixture's Shadowsocks inbound, not
    # the minimal placeholder.
    assert cfg.get_inbound("Shadowsocks TCP") is not None
    assert cfg.get_inbound("FALLBACK_PLACEHOLDER") is None


def test_resilient_load_falls_back_to_minimal_when_all_fail(tmp_path, monkeypatch):
    primary = tmp_path / "xray_config.json"
    primary.write_text("{ not valid json :::")  # corrupt, no .bak
    monkeypatch.setattr("app.xray.XRAY_JSON", str(primary))

    cfg = xray_module._load_config_resilient(api_port=18099)

    # Minimal built-in fallback, still controllable.
    assert cfg.get_inbound("FALLBACK_PLACEHOLDER") is not None
    assert cfg.get_inbound("API_INBOUND") is not None
