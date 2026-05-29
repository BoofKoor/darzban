"""Unit tests for ``app/xray/reconnect.py`` — backoff + circuit breaker.

Tests inject a fake clock; no real ``time.sleep`` or wall-clock reads.
"""

import threading

import pytest

from app.xray.reconnect import (
    ReconnectPolicy,
    discard_policy,
    get_policy,
    reset_all,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_all()
    yield
    reset_all()


# ---- fresh-policy invariants ------------------------------------------------

def test_fresh_policy_first_attempt_is_not_delayed():
    p = ReconnectPolicy()
    assert p.should_attempt(now=0.0) is True
    assert p.should_attempt(now=1234567.0) is True
    assert p.consecutive_failures == 0
    assert p.next_retry_at is None


def test_fresh_policy_circuit_closed():
    p = ReconnectPolicy()
    assert p.is_circuit_open() is False


# ---- backoff progression ----------------------------------------------------

def test_backoff_doubles_each_failure_until_cap():
    p = ReconnectPolicy(base=1.0, cap=300.0, circuit_threshold=999)
    expected = [1, 2, 4, 8, 16, 32, 64, 128, 256, 300, 300, 300]
    for i, want in enumerate(expected, start=1):
        backoff = p.on_failure(now=0.0)
        assert backoff == want, f"failure #{i}: expected {want}, got {backoff}"
        assert p.consecutive_failures == i
        assert p.current_backoff == want


def test_backoff_respects_custom_base_and_cap():
    p = ReconnectPolicy(base=2.0, cap=10.0, circuit_threshold=999)
    # 2 → 4 → 8 → 10 → 10
    assert p.on_failure(now=0.0) == 2.0
    assert p.on_failure(now=0.0) == 4.0
    assert p.on_failure(now=0.0) == 8.0
    assert p.on_failure(now=0.0) == 10.0
    assert p.on_failure(now=0.0) == 10.0


# ---- should_attempt cooldown ------------------------------------------------

def test_should_attempt_false_during_cooldown_true_after():
    p = ReconnectPolicy(base=4.0, cap=300.0)
    p.on_failure(now=100.0)  # next_retry_at = 104.0
    assert p.should_attempt(now=100.0) is False
    assert p.should_attempt(now=103.999) is False
    assert p.should_attempt(now=104.0) is True
    assert p.should_attempt(now=200.0) is True


def test_cooldown_advances_on_each_consecutive_failure():
    p = ReconnectPolicy(base=1.0, cap=300.0)
    p.on_failure(now=1000.0)
    assert p.next_retry_at == 1001.0
    p.on_failure(now=1001.0)
    assert p.next_retry_at == 1003.0  # 1001 + 2
    p.on_failure(now=1003.0)
    assert p.next_retry_at == 1007.0  # 1003 + 4


# ---- reset on success -------------------------------------------------------

def test_on_success_resets_failures_and_backoff_to_base():
    p = ReconnectPolicy(base=1.0, cap=300.0)
    for _ in range(7):
        p.on_failure(now=0.0)
    assert p.consecutive_failures == 7
    assert p.current_backoff == 64.0
    assert p.next_retry_at is not None

    p.on_success()
    assert p.consecutive_failures == 0
    assert p.current_backoff == 1.0
    assert p.next_retry_at is None
    assert p.should_attempt(now=0.0) is True

    # next failure starts the progression again from the bottom.
    assert p.on_failure(now=0.0) == 1.0


# ---- circuit breaker --------------------------------------------------------

def test_circuit_opens_at_threshold():
    p = ReconnectPolicy(base=1.0, cap=300.0, circuit_threshold=5)
    for _ in range(4):
        p.on_failure(now=0.0)
        assert p.is_circuit_open() is False
    p.on_failure(now=0.0)
    assert p.is_circuit_open() is True


def test_circuit_closes_on_success():
    p = ReconnectPolicy(base=1.0, cap=300.0, circuit_threshold=3)
    for _ in range(3):
        p.on_failure(now=0.0)
    assert p.is_circuit_open() is True
    p.on_success()
    assert p.is_circuit_open() is False


def test_circuit_stays_open_under_continued_failures_paced_at_cap():
    p = ReconnectPolicy(base=1.0, cap=300.0, circuit_threshold=5)
    for _ in range(20):
        p.on_failure(now=0.0)
    snap = p.snapshot()
    assert snap.circuit_open is True
    assert snap.current_backoff == 300.0
    assert snap.consecutive_failures == 20


# ---- snapshot ---------------------------------------------------------------

def test_snapshot_is_immutable_view():
    p = ReconnectPolicy(base=1.0, cap=300.0, circuit_threshold=5)
    p.on_failure(now=42.0)
    snap = p.snapshot()
    assert snap.consecutive_failures == 1
    assert snap.current_backoff == 1.0
    assert snap.next_retry_at == 43.0
    assert snap.circuit_open is False
    # snapshots are frozen
    with pytest.raises(Exception):
        snap.consecutive_failures = 99  # type: ignore[misc]


# ---- registry ---------------------------------------------------------------

def test_get_policy_creates_on_first_access():
    p = get_policy(node_id=7)
    assert isinstance(p, ReconnectPolicy)
    assert p.consecutive_failures == 0


def test_get_policy_returns_same_instance():
    p1 = get_policy(node_id=7)
    p2 = get_policy(node_id=7)
    assert p1 is p2


def test_get_policy_per_node_isolation():
    p1 = get_policy(node_id=1)
    p2 = get_policy(node_id=2)
    p1.on_failure(now=0.0)
    p1.on_failure(now=0.0)
    assert p1.consecutive_failures == 2
    assert p2.consecutive_failures == 0


def test_discard_policy_removes_and_next_get_returns_fresh():
    p1 = get_policy(node_id=5)
    p1.on_failure(now=0.0)
    p1.on_failure(now=0.0)
    discard_policy(node_id=5)
    p2 = get_policy(node_id=5)
    assert p2 is not p1
    assert p2.consecutive_failures == 0


def test_discard_policy_is_idempotent_for_unknown_id():
    discard_policy(node_id=9999)  # must not raise


# ---- thread safety: torn read regression ------------------------------------

def test_concurrent_failures_increment_counter_atomically():
    """Many threads hammering ``on_failure`` must produce exactly N failures
    (no lost updates). Detects a missing lock around the read-modify-write.
    """
    p = ReconnectPolicy(base=1.0, cap=300.0, circuit_threshold=10_000)
    n_threads = 32
    per_thread = 50

    def hammer():
        for _ in range(per_thread):
            p.on_failure(now=0.0)

    threads = [threading.Thread(target=hammer) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert p.consecutive_failures == n_threads * per_thread


def test_concurrent_success_failure_does_not_tear():
    """Mixed on_success / on_failure threads must leave the policy in a
    consistent state (counter and backoff agree).
    """
    p = ReconnectPolicy(base=1.0, cap=300.0, circuit_threshold=10_000)
    barrier = threading.Barrier(20)

    def hammer_fail():
        barrier.wait()
        for _ in range(100):
            p.on_failure(now=0.0)

    def hammer_succeed():
        barrier.wait()
        for _ in range(100):
            p.on_success()

    threads = [threading.Thread(target=hammer_fail) for _ in range(10)] + \
              [threading.Thread(target=hammer_succeed) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snap = p.snapshot()
    if snap.consecutive_failures == 0:
        assert snap.current_backoff == p.base
        assert snap.next_retry_at is None
    else:
        assert snap.current_backoff > 0
        assert snap.next_retry_at is not None
