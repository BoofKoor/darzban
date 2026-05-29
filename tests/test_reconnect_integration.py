"""Integration tests for the ReconnectPolicy hooks in
``app/xray/operations.py``.

These tests substitute a fake ``XRayNode`` (no sockets, no subprocess)
AND a recording stub for ``_change_node_status`` (no DB writes) and
assert that:

- A successful connect_node clears the policy (on_success).
- A failing connect_node bumps the policy (on_failure) and the
  Node.message passed to ``_change_node_status`` includes the
  structured suffix (``Retry in Ns (M consecutive failures...)``).
- A failing restart_node has the same effect.
- ``remove_node`` evicts the registry entry.
- The ``_connecting_nodes`` / ``_restarting_nodes`` guards prevent
  double policy updates when a tick fires while an attempt is in
  flight.

We bypass ``@threaded_function`` by calling the wrapped synchronous
inner via ``__wrapped__`` so outcomes are deterministic.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import xray
from app.models.node import NodeStatus
from app.xray import reconnect


@pytest.fixture(autouse=True)
def _clean_policy_registry():
    reconnect.reset_all()
    yield
    reconnect.reset_all()


@pytest.fixture
def fake_dbnode():
    """Minimal stand-in for ``app.db.models.Node`` as returned by
    ``crud.get_node_by_id``. operations.py only reads ``.id``,
    ``.name``, and ``.status`` on the happy path.
    """
    return SimpleNamespace(id=42, name="fake-node", status=NodeStatus.connecting)


@pytest.fixture
def recorded_status_calls():
    """Patch ``_change_node_status`` to record calls instead of
    hitting the DB. Returns the list of ``(node_id, status, message,
    version)`` tuples in the order they were made.
    """
    calls = []

    def _record(node_id, status, message=None, version=None):
        calls.append((node_id, status, message, version))

    with patch.object(xray.operations, "_change_node_status", side_effect=_record):
        yield calls


@pytest.fixture
def get_node_by_id_returns(fake_dbnode):
    """Patch ``crud.get_node_by_id`` (the only DB read on the path) so
    operations.py sees our fake node without a real query.
    """
    with patch("app.xray.operations.crud.get_node_by_id", return_value=fake_dbnode):
        yield


class _FakeNode:
    """Minimal in-memory stand-in for ``XRayNode``."""

    def __init__(self, *, start_raises=None, restart_raises=None,
                 starts_connected=False):
        self.connected = starts_connected
        self.started = starts_connected
        self.start_raises = start_raises
        self.restart_raises = restart_raises
        self.api = None

    def start(self, config):
        if self.start_raises:
            raise self.start_raises
        self.connected = True
        self.started = True

    def restart(self, config):
        if self.restart_raises:
            raise self.restart_raises
        self.connected = True
        self.started = True

    def disconnect(self):
        self.connected = False
        self.started = False

    def get_version(self):
        return "1.8.4-fake"


def _inject_fake_node(node_id, fake):
    xray.nodes[node_id] = fake


def _patch_config_include_db_users():
    return patch.object(xray.config, "include_db_users", return_value={})


@pytest.fixture
def patch_add_node_to_passthrough():
    """``connect_node`` calls ``operations.add_node`` whenever the
    existing entry in ``xray.nodes`` is missing OR not connected (see
    operations.py:208-212). The real ``add_node`` does a DB lookup
    (``get_tls()``) which would hit the global engine. We bypass it
    and return whatever fake was injected via ``_inject_fake_node``.
    """
    def _passthrough(dbnode):
        return xray.nodes[dbnode.id]

    with patch.object(xray.operations, "add_node", side_effect=_passthrough):
        yield


# ---- connect_node: success resets ------------------------------------------

def test_connect_node_success_resets_policy(fake_dbnode, patch_add_node_to_passthrough,
                                            get_node_by_id_returns,
                                            recorded_status_calls):
    fake = _FakeNode(starts_connected=False)
    _inject_fake_node(fake_dbnode.id, fake)

    policy = reconnect.get_policy(fake_dbnode.id)
    policy.on_failure(now=0.0)
    policy.on_failure(now=0.0)
    assert policy.consecutive_failures == 2

    with _patch_config_include_db_users():
        xray.operations.connect_node.__wrapped__(fake_dbnode.id)

    policy = reconnect.get_policy(fake_dbnode.id)
    assert policy.consecutive_failures == 0
    assert policy.next_retry_at is None
    assert policy.should_attempt(now=0.0) is True

    # Final status call should be "connected" with no message.
    final = recorded_status_calls[-1]
    assert final[1] == NodeStatus.connected
    assert final[3] == "1.8.4-fake"  # version


# ---- connect_node: failure bumps policy, writes structured message ----------

def test_connect_node_failure_bumps_policy_and_writes_message(patch_add_node_to_passthrough,
        fake_dbnode, get_node_by_id_returns, recorded_status_calls):
    fake = _FakeNode(start_raises=ConnectionError("connection refused"))
    _inject_fake_node(fake_dbnode.id, fake)

    with _patch_config_include_db_users():
        xray.operations.connect_node.__wrapped__(fake_dbnode.id)

    policy = reconnect.get_policy(fake_dbnode.id)
    assert policy.consecutive_failures == 1
    assert policy.current_backoff == policy.base

    # Status sequence: "connecting" (line 217) then "error" with our message.
    statuses = [c[1] for c in recorded_status_calls]
    assert statuses[-1] == NodeStatus.error
    err_message = recorded_status_calls[-1][2]
    assert "connection refused" in err_message
    assert "Retry in 1s" in err_message
    assert "1 consecutive failure" in err_message
    assert "circuit open" not in err_message


def test_connect_node_failure_message_pluralization(patch_add_node_to_passthrough,
        fake_dbnode, get_node_by_id_returns, recorded_status_calls):
    fake = _FakeNode(start_raises=RuntimeError("kaboom"))
    _inject_fake_node(fake_dbnode.id, fake)

    with _patch_config_include_db_users():
        xray.operations.connect_node.__wrapped__(fake_dbnode.id)
        xray.operations.connect_node.__wrapped__(fake_dbnode.id)

    err_messages = [c[2] for c in recorded_status_calls if c[1] == NodeStatus.error]
    assert "1 consecutive failure" in err_messages[0]
    assert "2 consecutive failures" in err_messages[1]  # plural


def test_connect_node_failure_surfaces_circuit_open(patch_add_node_to_passthrough,
        fake_dbnode, get_node_by_id_returns, recorded_status_calls):
    fake = _FakeNode(start_raises=ConnectionError("nope"))
    _inject_fake_node(fake_dbnode.id, fake)

    with _patch_config_include_db_users():
        for _ in range(5):  # CIRCUIT_THRESHOLD default = 5
            xray.operations.connect_node.__wrapped__(fake_dbnode.id)

    err_messages = [c[2] for c in recorded_status_calls if c[1] == NodeStatus.error]
    # First four should NOT mention circuit; fifth should.
    assert "circuit open" not in err_messages[0]
    assert "circuit open" not in err_messages[3]
    assert "circuit open" in err_messages[4]
    assert "5 consecutive failures" in err_messages[4]


# ---- restart_node: failure bumps policy ------------------------------------

def test_restart_node_failure_bumps_policy(
        fake_dbnode, get_node_by_id_returns, recorded_status_calls):
    fake = _FakeNode(starts_connected=True,
                     restart_raises=RuntimeError("xray crashed"))
    _inject_fake_node(fake_dbnode.id, fake)

    with _patch_config_include_db_users():
        xray.operations.restart_node.__wrapped__(fake_dbnode.id)

    policy = reconnect.get_policy(fake_dbnode.id)
    assert policy.consecutive_failures == 1

    err_message = recorded_status_calls[-1][2]
    assert "xray crashed" in err_message
    assert "Retry in 1s" in err_message


def test_restart_node_success_resets_policy(
        fake_dbnode, get_node_by_id_returns, recorded_status_calls):
    fake = _FakeNode(starts_connected=True)
    _inject_fake_node(fake_dbnode.id, fake)
    policy = reconnect.get_policy(fake_dbnode.id)
    policy.on_failure(now=0.0)
    policy.on_failure(now=0.0)

    with _patch_config_include_db_users():
        xray.operations.restart_node.__wrapped__(fake_dbnode.id)

    policy = reconnect.get_policy(fake_dbnode.id)
    assert policy.consecutive_failures == 0


# ---- in-flight guards ------------------------------------------------------

def test_connect_node_in_flight_guard_skips_second_call(
        fake_dbnode, get_node_by_id_returns, recorded_status_calls):
    fake = _FakeNode(start_raises=ConnectionError("slow fail"))
    _inject_fake_node(fake_dbnode.id, fake)

    xray.operations._connecting_nodes[fake_dbnode.id] = True
    try:
        with _patch_config_include_db_users():
            xray.operations.connect_node.__wrapped__(fake_dbnode.id)
    finally:
        xray.operations._connecting_nodes.pop(fake_dbnode.id, None)

    policy = reconnect.get_policy(fake_dbnode.id)
    assert policy.consecutive_failures == 0
    # And no DB writes happened.
    assert recorded_status_calls == []


def test_restart_node_in_flight_guard_skips_second_call(
        fake_dbnode, get_node_by_id_returns, recorded_status_calls):
    fake = _FakeNode(starts_connected=True,
                     restart_raises=RuntimeError("slow"))
    _inject_fake_node(fake_dbnode.id, fake)

    xray.operations._restarting_nodes[fake_dbnode.id] = True
    try:
        with _patch_config_include_db_users():
            xray.operations.restart_node.__wrapped__(fake_dbnode.id)
    finally:
        xray.operations._restarting_nodes.pop(fake_dbnode.id, None)

    policy = reconnect.get_policy(fake_dbnode.id)
    assert policy.consecutive_failures == 0
    assert recorded_status_calls == []


# ---- remove_node evicts policy state ---------------------------------------

def test_remove_node_evicts_policy(fake_dbnode):
    fake = _FakeNode(starts_connected=True)
    _inject_fake_node(fake_dbnode.id, fake)

    policy_before = reconnect.get_policy(fake_dbnode.id)
    policy_before.on_failure(now=0.0)
    policy_before.on_failure(now=0.0)
    assert policy_before.consecutive_failures == 2

    xray.operations.remove_node(fake_dbnode.id)

    policy_after = reconnect.get_policy(fake_dbnode.id)
    assert policy_after is not policy_before
    assert policy_after.consecutive_failures == 0
