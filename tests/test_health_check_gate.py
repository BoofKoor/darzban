"""Tests for the ``core_health_check`` policy gate added in v0.9.0
Task 4.

These tests verify the central behaviour change:
- A node whose ``ReconnectPolicy.should_attempt(now)`` is False is
  SKIPPED entirely by the health-check tick — no ``connect_node`` or
  ``restart_node`` call is made.
- A node whose policy is due (fresh, or cooldown expired) is attempted
  as before.
- The 10 s tick uses an INJECTABLE clock (`clock` kwarg), so tests
  pace ticks against a fake monotonic without real sleeps.
"""

from unittest.mock import patch

import pytest

from app import xray
from app.jobs.xray_core import core_health_check
from app.xray import reconnect


@pytest.fixture(autouse=True)
def _clean_policy_registry():
    reconnect.reset_all()
    yield
    reconnect.reset_all()


class _FakeNode:
    """Stand-in for XRayNode with the attributes core_health_check reads."""
    def __init__(self, connected: bool = False, started: bool = False):
        self.connected = connected
        self.started = started
        self.api = None


class _FakeApi:
    """Stand-in for node.api with a get_sys_stats that the tick probes."""
    def __init__(self, raises: BaseException | None = None):
        self._raises = raises

    def get_sys_stats(self, timeout=None):
        if self._raises:
            raise self._raises


@pytest.fixture
def healthy_main_core():
    """``xray.core`` is a lazy attribute. Replace with a stub that says
    "main core is up" so the tick doesn't try to restart it.
    """
    class _StubCore:
        started = True
    with patch.object(xray, "_core", _StubCore()):
        # also need to short-circuit the lazy initializer
        with patch("app.jobs.xray_core.xray.core", _StubCore()):
            yield


@pytest.fixture
def clear_nodes():
    """Empty the in-memory nodes dict before/after each test."""
    xray.nodes.clear()
    yield
    xray.nodes.clear()


# -----------------------------------------------------------------------------
# Core gate: not-due node is fully skipped
# -----------------------------------------------------------------------------

def test_not_due_node_is_skipped_no_reconnect_calls(healthy_main_core, clear_nodes):
    """A node whose policy says next_retry_at > now MUST NOT receive a
    connect_node or restart_node call this tick.
    """
    NODE_ID = 7
    xray.nodes[NODE_ID] = _FakeNode(connected=False)

    # Mark the policy as in cooldown.
    policy = reconnect.get_policy(NODE_ID)
    policy.on_failure(now=1000.0)  # next_retry_at = 1001.0

    fake_clock = lambda: 1000.5  # noqa: E731  before next_retry_at

    with patch.object(xray.operations, "connect_node") as m_connect, \
         patch.object(xray.operations, "restart_node") as m_restart:
        core_health_check(clock=fake_clock)

    m_connect.assert_not_called()
    m_restart.assert_not_called()
    # Policy state untouched (no on_failure / on_success).
    assert reconnect.get_policy(NODE_ID).consecutive_failures == 1


def test_due_node_is_attempted(healthy_main_core, clear_nodes):
    """When the clock has passed next_retry_at, the tick goes through
    to connect_node (because the fake node is not connected).
    """
    NODE_ID = 8
    xray.nodes[NODE_ID] = _FakeNode(connected=False)

    policy = reconnect.get_policy(NODE_ID)
    policy.on_failure(now=1000.0)  # next_retry_at = 1001.0

    fake_clock = lambda: 1001.0  # exactly at next_retry_at → due  # noqa: E731

    with patch.object(xray.operations, "connect_node") as m_connect, \
         patch.object(xray.operations, "restart_node") as m_restart, \
         patch.object(xray.config, "include_db_users", return_value={}):
        core_health_check(clock=fake_clock)

    m_connect.assert_called_once_with(NODE_ID, {})
    m_restart.assert_not_called()


def test_fresh_node_is_attempted_immediately(healthy_main_core, clear_nodes):
    """A node with NO recorded policy state is attempted on the first
    tick — newly-added/enabled nodes are never delayed.
    """
    NODE_ID = 9
    xray.nodes[NODE_ID] = _FakeNode(connected=False)

    with patch.object(xray.operations, "connect_node") as m_connect, \
         patch.object(xray.config, "include_db_users", return_value={}):
        core_health_check(clock=lambda: 0.0)

    m_connect.assert_called_once_with(NODE_ID, {})


def test_connected_node_with_healthy_api_does_no_reconnect(healthy_main_core, clear_nodes):
    """The classic happy path: connected, started, get_sys_stats OK —
    no connect_node / restart_node call.
    """
    NODE_ID = 10
    node = _FakeNode(connected=True, started=True)
    node.api = _FakeApi(raises=None)
    xray.nodes[NODE_ID] = node

    with patch.object(xray.operations, "connect_node") as m_connect, \
         patch.object(xray.operations, "restart_node") as m_restart:
        core_health_check(clock=lambda: 0.0)

    m_connect.assert_not_called()
    m_restart.assert_not_called()


def test_connected_node_with_failing_api_triggers_restart(healthy_main_core, clear_nodes):
    """get_sys_stats raises → restart_node is called (when policy is due).
    """
    NODE_ID = 11
    node = _FakeNode(connected=True, started=True)
    node.api = _FakeApi(raises=ConnectionError("gone"))
    xray.nodes[NODE_ID] = node

    with patch.object(xray.operations, "restart_node") as m_restart, \
         patch.object(xray.operations, "connect_node") as m_connect, \
         patch.object(xray.config, "include_db_users", return_value={}):
        core_health_check(clock=lambda: 0.0)

    m_restart.assert_called_once_with(NODE_ID, {})
    m_connect.assert_not_called()


def test_per_node_gating_independent(healthy_main_core, clear_nodes):
    """One node in cooldown, another due — only the due one gets attempted."""
    DUE = 21
    NOT_DUE = 22
    xray.nodes[DUE] = _FakeNode(connected=False)
    xray.nodes[NOT_DUE] = _FakeNode(connected=False)

    reconnect.get_policy(NOT_DUE).on_failure(now=1000.0)  # next_retry_at = 1001
    # DUE has fresh policy (None next_retry_at) → due immediately.

    with patch.object(xray.operations, "connect_node") as m_connect, \
         patch.object(xray.config, "include_db_users", return_value={}):
        core_health_check(clock=lambda: 1000.5)

    # Only the due node was attempted.
    assert m_connect.call_count == 1
    m_connect.assert_called_once_with(DUE, {})
