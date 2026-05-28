# Changelog

All notable changes to this fork are recorded here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project does not strictly follow SemVer because compatibility with the
upstream Marzban API surface takes precedence.

## v0.9.0 (unreleased)

The v0.9.0 release is a stability-focused refresh. See
`docs/V0.9.0_DECISIONS.md` for the full scope and rationale and
`docs/CODEBASE_MAP.md` for the codebase survey that drove it.

### Task 1 — Foundation

#### Added
- **Test infrastructure.** `pytest` + `pytest-asyncio` + `pytest-mock` +
  `httpx` + `ruff` under `requirements-dev.txt`. New `pyproject.toml`
  configures pytest (`testpaths=["tests"]`, `asyncio_mode="auto"`) and ruff
  (`line-length=100`, target Python 3.12, sensible `E/F/I/B` rule set with
  pervasive legacy categories silenced and tracked in `CODEBASE_MAP §6`).
- **`tests/` scaffolding** with shared fixtures (`db_session`, `client`)
  backed by SQLite in-memory and a `TestClient` whose `get_db` is
  overridden to the test session. Smoke tests: app imports, public
  endpoint responds, DB session fixture works.
- **GitHub Actions CI** (`.github/workflows/ci.yml`): on push and PR,
  Python 3.12, installs `requirements.txt` + `requirements-dev.txt`, runs
  `ruff check .`, runs `pytest -v`, with pip caching.
- **`DEVELOPING.md`** (and a README pointer): local dev setup, how to run
  tests and lint, how to reproduce CI checks locally.
- Regression tests for the `usage_coefficient` create bug and the JWT
  bare-except tightening.

#### Changed
- **Xray binary is now pinned** at `v26.2.6` via a vendored
  `scripts/install_xray.sh` (replaces the build-time `curl … |  bash` of
  upstream `install_latest_xray.sh`, which silently tracked latest).
  `Dockerfile` exposes `ARG XRAY_VERSION` so rebuilds are deterministic;
  override with `docker build --build-arg XRAY_VERSION=vX.Y.Z`.
  `scripts/install_latest_xray.sh` is retained as a **deprecated** symlink
  to the new script and will be removed in v1.0.
- **`main.py` now enforces single-worker startup**: if `UVICORN_WORKERS`
  env or `--workers` CLI is set to `>1`, the process logs a clear error
  explaining the in-process singleton constraints (APScheduler, XRayCore,
  XRayAPI, the `nodes` map) and exits with code 1. Multi-worker support
  is a v1.0+ goal (`V0.9.0_DECISIONS.md` Q11).

#### Fixed
- **`app/jobs/review_users.py:add_notification_reminders`** —
  `now: datetime = datetime.utcnow()` was evaluated once at module import,
  so every later call that omitted `now` saw the frozen import-time
  timestamp instead of "now". Default to `None`, resolve at call time.
- **`app/db/crud.py:create_node`** — `usage_coefficient` from the
  `NodeCreate` payload was dropped and every new node got the DB default
  `1.0` regardless of admin input. Now persisted on creation. Regression
  test added (`tests/test_crud_node.py`).
- **`app/utils/jwt.py:get_subscription_payload`** — the bare `except:`
  around the token base64-decode block now catches only
  `(binascii.Error, UnicodeDecodeError)`. Behavior for invalid tokens is
  unchanged (still returns `None`); unrelated programmer errors are no
  longer masked. Tracked under `CODEBASE_MAP §6.5`.

### Deprecated (notice only — removal targeted for v1.0)
- `scripts/install_latest_xray.sh` (use `scripts/install_xray.sh --version`).

### Upgrade notes
- No DB migration changes. No subscription URL changes. No config-file
  changes. Drop-in upgrade from 0.8.4.
- If your image build relied on `install_latest_xray.sh` being fetched from
  the upstream Gozargah/Marzban-scripts repo, you can either keep that
  external dependency (the symlink keeps the old name working locally) or
  pin a specific version via `--build-arg XRAY_VERSION=vX.Y.Z`.
- If you previously started uvicorn with `--workers N` where `N > 1`, the
  process will refuse to start. Drop the flag — multi-worker was never
  supported (see the pre-existing comment at `main.py:48-49`).
