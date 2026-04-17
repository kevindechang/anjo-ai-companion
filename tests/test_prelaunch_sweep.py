"""Regression tests from pre-launch sweep (auth webhooks, reflection dedupe)."""
from __future__ import annotations


def test_should_skip_auth_includes_revenuecat_webhook():
    from anjo.dashboard.auth import should_skip_auth

    assert should_skip_auth("/api/billing/webhook")


def test_reflection_session_claim_idempotent():
    from anjo.dashboard.background_tasks import reflection_session_claim

    sid = "sess-claim-test"
    assert reflection_session_claim(sid) is True
    assert reflection_session_claim(sid) is False


def test_revenuecat_webhook_unauthenticated_reaches_handler(monkeypatch):
    """Auth middleware must not 401 before RevenueCat secret check."""
    from fastapi.testclient import TestClient

    from anjo.dashboard.app import app

    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", "rc_wh_secret_test")
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(
        "/api/billing/webhook",
        headers={"Authorization": "Bearer wrong"},
        json={"event": {"type": "EXPIRATION", "app_user_id": "u1"}},
    )
    assert r.status_code == 401
    detail = r.json().get("detail", "")
    assert "Invalid" in detail or "webhook" in detail.lower()


def test_coerce_llm_bool_string_false():
    from anjo.graph.nodes import _coerce_llm_bool

    assert _coerce_llm_bool("false", True) is False
    assert _coerce_llm_bool("true", False) is True
    assert _coerce_llm_bool(False, True) is False
