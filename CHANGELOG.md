# Changelog

All notable changes to this fork are recorded here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project does not strictly follow SemVer because compatibility with the
upstream Marzban API surface takes precedence.

## [0.9.0] - 2026-05-30

A stability-focused refresh of the panel. Four task tracks landed under
the v0.9.0 umbrella; together they target the highest-friction items
identified in the codebase survey (`docs/CODEBASE_MAP.md`) under the
constraint of full drop-in compatibility with 0.8.4 deployments.

**Compatibility:** **no DB migrations** — Alembic head is unchanged
(`2b231de97dc3`); a v0.8.4 SQLite/PostgreSQL/MySQL database boots
cleanly under v0.9.0 with zero manual steps. No subscription URL
changes. No required config-file changes. Verified drop-in upgrade.

### Highlights

- **Node reliability (Task 4).** The 10 s health-check loop no longer
  hammers down nodes every tick — per-node exponential backoff (1s →
  2s → 4s → … → 300s cap) plus a circuit breaker (default 5
  consecutive failures) replace the fixed-cadence retry. Operator-
  initiated reconnects (REST, telegram, lifespan boot) bypass the
  gate and run immediately. Backoff state lives in a module-level
  registry so it survives the `XRayNode` object recreation that
  happens on every reconnect. A late-cycle bug where backoff didn't
  accumulate (registry was being wiped on the internal
  add_node→remove_node recreate) was caught on a real-server boot
  test and fixed before release. The rpyc no-sleep retry spin
  (`node.py:351-373`) is gone — the health-check policy is the single
  retry source. `Node.message` on failure now includes
  `"Retry in Ns (M consecutive failures[; circuit open])"`.
- **Reliability infrastructure (Task 3).** All `@app.on_event`
  handlers migrated to FastAPI's `lifespan` (registration order
  preserved exactly). `import app` now performs **zero** subprocess /
  HTTP / TCP-socket I/O — the `xray version` subprocess, the public
  IP HTTP probes (~15 s of `get_public_ip()` at import), and the
  free-port scan in `app/xray/__init__.py` are all deferred. A new
  `tests/test_import_isolation.py` forks a fresh interpreter with
  those calls blocked and asserts `import app` succeeds — locking the
  invariant in CI. Node connect/restart failures log at `ERROR` with
  `exc_info=True` (was `INFO` with no traceback). New optional
  `LOG_FORMAT=json` env var enables stdlib-only structured logging;
  default `text` preserves uvicorn's current output. rpyc node
  transport gains a one-shot per-process `DEPRECATED` warning;
  removal target v1.0 (see `docs/migrating-from-rpyc.md`).
- **Decoupling (Task 2).** `app/db/models.py` and `app/models/*` no
  longer import `app/xray` directly. Inbound metadata is resolved
  through a new `InboundLookup` abstraction in `app/db/lookups.py`
  (default `XrayConfigLookup` proxies to `app.xray.config` with lazy
  inner imports). Test seam: tests can inject a stub lookup
  (`FakeInboundLookup`) without spinning up the Xray subsystem.
- **Foundation, CI, and bug fixes (Task 1).**
  - **Xray binary pinned at v26.2.6** via a vendored
    `scripts/install_xray.sh` (replaces the build-time `curl … | bash`
    of upstream `install_latest_xray.sh`, which silently tracked
    latest). Dockerfile exposes `ARG XRAY_VERSION` for deterministic
    rebuilds.
  - **Test infrastructure**: pytest/pytest-asyncio/pytest-mock/httpx/
    ruff under `requirements-dev.txt`; `pyproject.toml` configures
    pytest and ruff; `tests/` scaffolding with SQLite-in-memory
    fixtures and a `TestClient` whose `get_db` is overridden.
  - **GitHub Actions CI**: `ruff check` + `pytest -v` on push/PR,
    plus a separate `docker-build` job that runs `docker build`
    end-to-end so "CI green" includes "the image builds".
  - **Single-worker enforcement** in `main.py` (`UVICORN_WORKERS > 1`
    exits with a clear error explaining the in-process singletons;
    multi-worker support is a v1.0+ goal).
  - **Bug fixes** with regression tests:
    - `app/db/crud.py:create_node` — `usage_coefficient` from the
      payload is now persisted on creation (was silently dropped;
      every node got the DB default 1.0).
    - `app/jobs/review_users.py` — `now: datetime = datetime.utcnow()`
      default was evaluated at module import (frozen timestamp); now
      resolved at call time.
    - `app/utils/jwt.py` — bare `except:` around token base64-decode
      narrowed to `(binascii.Error, UnicodeDecodeError)`.
    - **Dockerfile setuptools pin** (`<81`) so APScheduler's
      `pkg_resources` import doesn't break the production image build
      (same root cause as the CI bootstrap pin; the Dockerfile was
      missed in the initial Task 1 fix).

### New optional environment variables

| Var | Default | Effect |
|---|---|---|
| `NODE_RECONNECT_BACKOFF_BASE` | `1.0` | First-failure cooldown (seconds). |
| `NODE_RECONNECT_BACKOFF_CAP` | `300.0` | Maximum cooldown (seconds). |
| `NODE_RECONNECT_CIRCUIT_THRESHOLD` | `5` | Consecutive failures before the circuit is reported "open" in `Node.message`. Retries continue at the cap. |
| `LOG_FORMAT` | `text` | Set to `json` for stdlib-only structured logging. |
| `XRAY_VERSION` (build arg) | `v26.2.6` | Pinned Xray version baked into the Docker image. |

All defaults reproduce the 0.8.4 behavior except where called out as
intentional changes below.

### Behaviour changes (operator-visible)

- **A permanently down node no longer floods logs.** The health-check
  tick skips not-due nodes; once the circuit opens, retries continue
  at the cap (300 s by default), not every 10 s.
- **Node failure logs now include a traceback.** `connect_node` and
  `restart_node` errors moved from `INFO` (message only) to `ERROR`
  with `exc_info=True`.
- **`Node.message` on failure has a structured suffix** appended
  after the original exception text: `"Retry in Ns (M consecutive
  failures[; circuit open])."`. Existing readers (admin UI, telegram
  bot, `NodeResponse` passthrough) treat the field as opaque text and
  are unaffected.
- **`UVICORN_WORKERS > 1` now refuses to start.** Multi-worker was
  never actually supported (APScheduler, XRayCore, the in-process
  `nodes` map are all singletons); the silent failure mode is now a
  loud one.
- **rpyc node transport is deprecated.** A one-shot warning per
  process fires when an rpyc node connects. Behaviour is unchanged
  in v0.9.x; removal target v1.0. See
  [`docs/migrating-from-rpyc.md`](docs/migrating-from-rpyc.md).
- **`scripts/install_latest_xray.sh`** is a deprecated symlink to
  `scripts/install_xray.sh`. Removal target v1.0.

### Internal

- `@threaded_function` now uses `functools.wraps` (preserves
  `__wrapped__` for deterministic test invocation).
- `tests/conftest.py` no longer monkeypatches `subprocess`, `requests`,
  or `socket` at import time; the dual-engine workaround for the JWT
  secret was retired in favour of an `app.utils.jwt.get_secret_key`
  override fixture.

### Upgrade notes

- **Drop-in:** no DB migrations, no URL changes, no required env
  changes. Boot a v0.9.0 image against your 0.8.4 database.
- If your image build relied on `install_latest_xray.sh` being
  fetched from the upstream `Gozargah/Marzban-scripts` repo, either
  keep that external dependency (the symlink keeps the old name
  working locally) or pin a specific version via
  `--build-arg XRAY_VERSION=vX.Y.Z`.
- If you previously started uvicorn with `--workers N` where `N > 1`,
  drop the flag — multi-worker was never supported (see the
  pre-existing comment at `main.py:48-49`); v0.9.0 now refuses to
  start instead of silently misbehaving.
- If you observe a new `DEPRECATED` log entry at panel startup, at
  least one of your nodes is on the rpyc transport — see
  `docs/migrating-from-rpyc.md` for the (panel-transparent)
  node-side switch to the REST/uvicorn agent.

### Tests

24 tests at 0.8.4 → **60 tests** at 0.9.0. Highlights:
- `tests/test_import_isolation.py` — proves `import app` does no
  subprocess / network / socket I/O (locks the Task 3 invariant).
- `tests/test_reconnect_policy.py` (18) — backoff progression,
  cooldown, reset, circuit open/close, two concurrency regression
  tests (lost-update detection under heavy contention).
- `tests/test_reconnect_integration.py` (11) — policy hooks fire,
  `Node.message` formatting (pluralization, circuit-open suffix),
  in-flight guards, **plus the late-cycle regression test that
  reproduces the real-server flow** (`add_node` runs for real; backoff
  must accumulate across 6 successive failures).
- `tests/test_health_check_gate.py` (6) — not-due node fully skipped,
  due attempted, fresh attempted immediately.
- `tests/test_log_setup.py` (5) — JSON formatter coverage.
- `tests/test_lookups.py` (6) + `tests/test_user_inbounds.py` (5) —
  Task 2 contract tests (the inbound lookup seam).

All tests use injected clocks where time-sensitive; zero real
`time.sleep`.

