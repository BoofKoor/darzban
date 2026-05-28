"""Shared test fixtures.

This module performs two environment fixups *before* importing the
application:

1. Sets ``SQLALCHEMY_DATABASE_URL`` to an in-memory SQLite database so the
   engine created at import time in ``app.db.base`` is harmless.
2. Stubs ``subprocess.check_output`` for the ``xray version`` invocation
   that ``app.xray.core.XRayCore.__init__`` makes at import time. Without
   this, importing ``app`` requires the Xray binary to be installed.

Both fixups are scoped to the test session only.
"""

import os
import socket
import subprocess
import sys

# --- Environment fixups (must run before any `from app import ...`) ---
os.environ.setdefault("SQLALCHEMY_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("XRAY_JSON", "./xray_config.json")
os.environ.setdefault("TELEGRAM_API_TOKEN", "")

_real_check_output = subprocess.check_output


def _check_output_with_xray_stub(cmd, *args, **kwargs):
    if isinstance(cmd, (list, tuple)) and len(cmd) >= 2:
        exe = str(cmd[0])
        sub = str(cmd[1])
        if "xray" in exe and sub == "version":
            return b"Xray 1.8.4 (Xray, Penetrates Everything.) Stub\n"
        if "xray" in exe and sub == "x25519":
            return (
                b"Private key: stub-private-key\n"
                b"Public key: stub-public-key\n"
            )
    return _real_check_output(cmd, *args, **kwargs)


subprocess.check_output = _check_output_with_xray_stub  # type: ignore[assignment]

# `app/subscription/share.py` calls `get_public_ip()` at module import time,
# which makes outbound HTTP requests. In a sandboxed test environment those
# calls either fail or return non-IP content that propagates a ValueError out
# of the (incomplete) exception handling in `app/utils/system.py`. We stub
# requests.get to raise so every HTTP-based branch falls through to the UDP
# socket trick (which is local-only) and ultimately returns 127.0.0.1.
import requests  # noqa: E402

_real_requests_get = requests.get


def _stub_requests_get(*args, **kwargs):
    raise requests.exceptions.ConnectionError("blocked in tests")


requests.get = _stub_requests_get  # type: ignore[assignment]

# Make the socket-based fallback in `get_public_ip` deterministic: prevent it
# from doing real network work and force the function to return 127.0.0.1.
_real_socket_connect = socket.socket.connect


def _no_connect(self, *args, **kwargs):
    raise OSError("blocked in tests")


socket.socket.connect = _no_connect  # type: ignore[assignment]

# Now safe to import the app and DB layers.
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

# Importing `app` triggers app/__init__.py which wires routers, jobs, and
# the Xray module singletons. The stubs above keep that import side-effect-free.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app as fastapi_app  # noqa: E402
from app.db import get_db  # noqa: E402
from app.db.base import Base  # noqa: E402


@pytest.fixture(scope="session")
def test_engine():
    """A single in-memory SQLite engine shared by all sessions in a test run.

    ``StaticPool`` + ``check_same_thread=False`` keeps the in-memory database
    alive across the TestClient threadpool boundary.
    """
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(test_engine):
    """Yield a transactional session that rolls back on teardown."""
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(test_engine):
    """FastAPI TestClient with the ``get_db`` dependency pointed at the
    in-memory engine.
    """
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def _override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    fastapi_app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
