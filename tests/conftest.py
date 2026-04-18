"""Shared pytest fixtures — isolates all I/O to a temp directory per test."""

from __future__ import annotations

import os

import pytest

# Set dev env before any app imports so _get_secret() doesn't raise
os.environ.setdefault("ANJO_ENV", "dev")
os.environ.setdefault("ANJO_SECRET", "test_secret_key_for_pytest")
os.environ.setdefault("ANJO_ADMIN_SECRET", "test_admin_key")


# ── App (imported once) ───────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def _app():
    from anjo.dashboard.app import app

    return app


# ── Isolation (per test) ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Redirect SQLite db and all file-based paths to tmp; clear in-memory state."""
    import sys

    import anjo.core.db as _db
    import anjo.core.history as _hist
    import anjo.core.self_core as _sc
    import anjo.dashboard.session_store as _sess
    import anjo.memory.journal as _journal
    import anjo.reflection.log as _rlog

    # Close any open db connection, redirect to fresh temp db, then reset again
    # so the next get_db() call opens a new connection at the temp path.
    # Also reset the schema initialization flag so the new DB gets tables.
    _db.reset()
    monkeypatch.setattr(_db, "_DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(_db, "_schema_initialized", False)
    _db.reset()

    # File-based storage (self_core, history, reflection log, session files)
    monkeypatch.setattr(_sc, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(_hist, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(_rlog, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(_sess, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(_journal, "_DATA_ROOT", tmp_path)

    # Remove real API keys so registration auto-verifies (no actual email sent)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("PADDLE_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    _sess._sessions.clear()

    import anjo.dashboard.background_tasks as _bg

    _bg._REFLECTED_SESSIONS.clear()
    _bg._QUICK_FACTS_DONE.clear()
    _bg._MID_REFLECT_LOCK.clear()

    # Clear rate-limit and token-revocation state — but only if the modules are
    # already imported (importing app.py here would trigger load_dotenv() which
    # loads CLAUDE_CODE_USE_BEDROCK=1 and breaks llm.USE_BEDROCK for other tests).
    if "anjo.dashboard.middleware.rate_limit" in sys.modules:
        sys.modules["anjo.dashboard.middleware.rate_limit"]._rl_hits.clear()
    if "anjo.dashboard.auth" in sys.modules:
        sys.modules["anjo.dashboard.auth"]._revoked_tokens.clear()

    yield

    # Close temp db connection so the file can be cleaned up by tmp_path
    _db.reset()


# ── HTTP clients ──────────────────────────────────────────────────────────────


@pytest.fixture
def client(_app):
    from fastapi.testclient import TestClient

    return TestClient(_app, raise_server_exceptions=False)


@pytest.fixture
def auth_client(client):
    """Client pre-authenticated as 'testuser'."""
    client.post(
        "/register",
        data={
            "username": "testuser",
            "password": "testpass123",
            "email": "test@example.com",
        },
    )
    client.post(
        "/login",
        data={
            "username": "testuser",
            "password": "testpass123",
        },
    )
    return client
