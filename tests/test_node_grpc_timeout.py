"""The gRPC channel-ready wait in ``app/xray/node.py`` must use the
configurable ``NODE_GRPC_READY_TIMEOUT`` (default 30s), not the old
hardcoded 5s.

Regression guard for the production incident: on a panel restart a node
brings its Xray gRPC API back up under a large (16k-user) config, which
took longer than the 5s wait — so the wait timed out
(``Failed to connect to node's API``) and the reconnect backoff climbed
to its cap, leaving the node disconnected until a manual Reconnect.

These tests bypass ``ReSTXRayNode.__init__`` (no temp files, sockets, or
subprocess) and stub every leaf the start()/restart() gRPC path touches,
so we assert purely on the ``timeout`` handed to
``grpc.channel_ready_future(...).result()``.
"""

from types import SimpleNamespace

import pytest

from app.xray import node as node_mod


class _FakeFuture:
    def __init__(self):
        self.timeout = "<unset>"

    def result(self, timeout=None):
        self.timeout = timeout
        return True


@pytest.fixture
def rest_node(monkeypatch):
    n = node_mod.ReSTXRayNode.__new__(node_mod.ReSTXRayNode)
    n.address = "127.0.0.1"
    n.api_port = 62051
    n._node_cert = "CERT-PEM"
    n._session_id = "sid"
    n._api = None
    n._started = False

    # connected -> True so start()/restart() skip connect() (network).
    monkeypatch.setattr(type(n), "connected", property(lambda self: True))
    # passthrough config prep; the config only needs to_json().
    monkeypatch.setattr(n, "_prepare_config", lambda cfg: cfg)
    # /start and /restart must not hit the network.
    monkeypatch.setattr(n, "make_request", lambda *a, **k: {"ok": True})
    # don't build a real grpc channel.
    monkeypatch.setattr(
        node_mod, "XRayAPI", lambda **kw: SimpleNamespace(_channel=object())
    )
    return n


def _config():
    return SimpleNamespace(to_json=lambda: "{}")


def test_start_waits_with_configured_grpc_timeout(rest_node, monkeypatch):
    fut = _FakeFuture()
    monkeypatch.setattr(node_mod.grpc, "channel_ready_future", lambda ch: fut)

    rest_node.start(_config())

    assert fut.timeout == node_mod.NODE_GRPC_READY_TIMEOUT
    assert fut.timeout != 5  # regression guard: not the old hardcoded value


def test_restart_waits_with_configured_grpc_timeout(rest_node, monkeypatch):
    fut = _FakeFuture()
    monkeypatch.setattr(node_mod.grpc, "channel_ready_future", lambda ch: fut)

    rest_node.restart(_config())

    assert fut.timeout == node_mod.NODE_GRPC_READY_TIMEOUT
    assert fut.timeout != 5
