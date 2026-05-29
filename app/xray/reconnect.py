"""Per-node reconnect policy: exponential backoff + circuit breaker.

The panel's `core_health_check` job ran every 10 s and retried every
down node every tick, forever — see CODEBASE_MAP §6.4. v0.9.0 Task 4
replaces that with a per-node ``ReconnectPolicy`` that:

- Doubles the wait after each consecutive failure
  (BASE * 2^(n-1)), capped at CAP. health_check consults
  ``should_attempt(now)`` and skips nodes whose cooldown hasn't
  elapsed — this is the core fix.
- Opens the circuit at ``CIRCUIT_THRESHOLD`` consecutive failures.
  Retries continue (the loop never gives up), but stay paced at the
  cap; ``is_circuit_open()`` lets the caller surface that state to
  ``Node.message`` so operators can see the node is firmly down.
- Resets to base on any successful connect (including operator-forced
  ``POST /api/node/{id}/reconnect`` that happens to work).

Design choices (see Task 4 discovery report):

- **State lives in a module-level registry** keyed by node_id, NOT
  on the ``XRayNode`` instance. ``XRayNode`` objects get replaced by
  ``operations.add_node`` during the reconnect path; pinning policy
  state to the object would lose the failure counter exactly when we
  need it. The registry survives object churn; ``discard_policy()``
  is the single eviction hook used by ``operations.remove_node``.
- **Clock is injected**, not global. Every method that consults or
  advances time takes ``now: float`` as an explicit argument. Tests
  pass a fake clock; production passes ``time.monotonic()``.
- **Thread safety**: ``connect_node`` and ``restart_node`` run via
  ``@threaded_function`` and can race with each other (operator-pushed
  reconnect overlapping a health-check tick). Each ``ReconnectPolicy``
  carries its own ``threading.Lock`` and every method does the full
  read-modify-write inside the lock to prevent torn reads of
  ``next_retry_at`` / ``consecutive_failures`` / ``current_backoff``.
  The registry itself has a separate lock for dict insert/delete.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from config import (
    NODE_RECONNECT_BACKOFF_BASE,
    NODE_RECONNECT_BACKOFF_CAP,
    NODE_RECONNECT_CIRCUIT_THRESHOLD,
)

Clock = Callable[[], float]


@dataclass
class ReconnectPolicy:
    """Per-node reconnect state.

    All public methods are thread-safe. The clock is NEVER read inside
    the policy — callers pass ``now`` explicitly so tests can use a
    fake clock without monkeypatching ``time``.
    """

    base: float = NODE_RECONNECT_BACKOFF_BASE
    cap: float = NODE_RECONNECT_BACKOFF_CAP
    circuit_threshold: int = NODE_RECONNECT_CIRCUIT_THRESHOLD

    consecutive_failures: int = 0
    current_backoff: float = field(default=NODE_RECONNECT_BACKOFF_BASE)
    next_retry_at: Optional[float] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def should_attempt(self, now: float) -> bool:
        """Return True if the caller should try to (re)connect this node.

        A fresh policy with no recorded failures returns True immediately
        — the first attempt for a newly-added/enabled node is NEVER
        delayed.
        """
        with self._lock:
            return self.next_retry_at is None or now >= self.next_retry_at

    def on_failure(self, now: float) -> float:
        """Record a failed attempt; advance the cooldown.

        Returns the new ``current_backoff`` (seconds until next retry)
        so the caller can surface it via ``Node.message``.
        """
        with self._lock:
            self.consecutive_failures += 1
            # base * 2^(n-1), capped at `cap`. For base=1, cap=300:
            # n=1 → 1s, n=2 → 2s, ... n=9 → 256s, n>=10 → 300s.
            # The exponent is clamped at 30 (~1e9) so a long-down node
            # doesn't overflow the float computation after thousands of
            # consecutive failures.
            exponent = min(self.consecutive_failures - 1, 30)
            self.current_backoff = min(self.base * (2 ** exponent), self.cap)
            self.next_retry_at = now + self.current_backoff
            return self.current_backoff

    def on_success(self) -> None:
        """Record a successful (re)connect; clear backoff state.

        After this call, ``should_attempt`` returns True immediately
        and ``current_backoff`` is back to ``base``. The next failure
        (if any) starts the progression from the bottom.
        """
        with self._lock:
            self.consecutive_failures = 0
            self.current_backoff = self.base
            self.next_retry_at = None

    def is_circuit_open(self) -> bool:
        """Return True once we've hit ``circuit_threshold`` consecutive
        failures. Retries still happen (paced at the cap), but
        ``Node.message`` can surface ``circuit open`` so operators see
        the node is firmly down.
        """
        with self._lock:
            return self.consecutive_failures >= self.circuit_threshold

    def snapshot(self) -> "PolicySnapshot":
        """Return an immutable view of the current state.

        Useful for tests and for formatting ``Node.message`` without
        holding the lock across string formatting.
        """
        with self._lock:
            return PolicySnapshot(
                consecutive_failures=self.consecutive_failures,
                current_backoff=self.current_backoff,
                next_retry_at=self.next_retry_at,
                circuit_open=self.consecutive_failures >= self.circuit_threshold,
            )


@dataclass(frozen=True)
class PolicySnapshot:
    consecutive_failures: int
    current_backoff: float
    next_retry_at: Optional[float]
    circuit_open: bool


# --- Module-level registry ---------------------------------------------------
#
# Keyed by Node.id. Lifetime: created lazily on first access via
# ``get_policy``; evicted by ``discard_policy`` when ``operations.remove_node``
# removes the node from the in-memory map. Panel restart clears all state
# (a restart is itself a manual intervention, so resetting backoff on boot
# is the right default).

_policies: Dict[int, ReconnectPolicy] = {}
_registry_lock = threading.Lock()


def get_policy(node_id: int) -> ReconnectPolicy:
    """Get the policy for ``node_id``, creating one on first access."""
    with _registry_lock:
        policy = _policies.get(node_id)
        if policy is None:
            policy = ReconnectPolicy()
            _policies[node_id] = policy
        return policy


def discard_policy(node_id: int) -> None:
    """Drop the policy for ``node_id`` (called from ``remove_node``)."""
    with _registry_lock:
        _policies.pop(node_id, None)


def reset_all() -> None:
    """Empty the registry. For tests."""
    with _registry_lock:
        _policies.clear()


def default_clock() -> float:
    """Monotonic clock used by the production health-check loop."""
    return time.monotonic()


__all__ = [
    "Clock",
    "PolicySnapshot",
    "ReconnectPolicy",
    "default_clock",
    "discard_policy",
    "get_policy",
    "reset_all",
]
