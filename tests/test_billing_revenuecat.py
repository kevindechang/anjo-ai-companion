"""RevenueCat billing integration tests."""
from __future__ import annotations

import json


WEBHOOK_SECRET = "test_rc_webhook_secret"


def _post_webhook(client, event: dict, secret: str = WEBHOOK_SECRET):
    body = json.dumps({"event": event}).encode()
    return client.post(
        "/api/billing/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
        },
    )


# ── Status endpoint ───────────────────────────────────────────────────────────

def test_billing_status_requires_auth(client):
    r = client.get("/api/billing/status")
    assert r.status_code == 401


def test_billing_status_free_user(auth_client):
    r = auth_client.get("/api/billing/status")
    assert r.status_code == 200
    d = r.json()
    assert d["tier"] == "free"
    assert d["subscribed"] is False
    assert d["daily_limit"] == 20
    assert d["messages_remaining"] == 20
    assert d["message_credits"] == 0


# ── Config endpoint ───────────────────────────────────────────────────────────

def test_billing_config_requires_auth(client):
    r = client.get("/api/billing/config")
    assert r.status_code == 401


def test_billing_config_disabled(auth_client, monkeypatch):
    import anjo.dashboard.routes.billing_routes as br
    monkeypatch.setattr(br, "PAYMENTS_ENABLED", False)
    r = auth_client.get("/api/billing/config")
    assert r.status_code == 200
    assert r.json()["payments_enabled"] is False


def test_billing_config_enabled(auth_client, monkeypatch):
    import anjo.dashboard.routes.billing_routes as br
    monkeypatch.setattr(br, "PAYMENTS_ENABLED", True)
    r = auth_client.get("/api/billing/config")
    assert r.status_code == 200
    d = r.json()
    assert d["payments_enabled"] is True
    assert "products" in d
    assert "pro_monthly" in d["products"]
    assert "premium_monthly" in d["products"]


# ── Webhook auth ──────────────────────────────────────────────────────────────

def test_webhook_rejects_missing_secret(client, monkeypatch):
    monkeypatch.delenv("REVENUECAT_WEBHOOK_SECRET", raising=False)
    r = client.post(
        "/api/billing/webhook",
        content=b"{}",
        headers={"Content-Type": "application/json", "Authorization": "Bearer anything"},
    )
    assert r.status_code == 500


def test_webhook_rejects_bad_secret(client, monkeypatch):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", WEBHOOK_SECRET)
    r = client.post(
        "/api/billing/webhook",
        content=b'{"event": {}}',
        headers={"Content-Type": "application/json", "Authorization": "Bearer wrong_secret"},
    )
    assert r.status_code == 401


def test_webhook_accepts_valid_secret(client, monkeypatch):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", WEBHOOK_SECRET)
    r = _post_webhook(client, {})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── Subscription activated (INITIAL_PURCHASE) ─────────────────────────────────

def test_webhook_initial_purchase_pro(client, monkeypatch, auth_client):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", WEBHOOK_SECRET)

    from anjo.core.db import get_db
    row = get_db().execute("SELECT user_id FROM users WHERE username = 'testuser'").fetchone()
    user_id = row["user_id"]

    r = _post_webhook(client, {
        "type": "INITIAL_PURCHASE",
        "app_user_id": user_id,
        "product_id": "anjo_pro_monthly",
        "expiration_at_ms": 9999999999000,
    })
    assert r.status_code == 200

    status = auth_client.get("/api/billing/status").json()
    assert status["tier"] == "pro"
    assert status["subscribed"] is True
    assert status["daily_limit"] == 60


def test_webhook_initial_purchase_premium(client, monkeypatch, auth_client):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", WEBHOOK_SECRET)

    from anjo.core.db import get_db
    row = get_db().execute("SELECT user_id FROM users WHERE username = 'testuser'").fetchone()
    user_id = row["user_id"]

    _post_webhook(client, {
        "type": "INITIAL_PURCHASE",
        "app_user_id": user_id,
        "product_id": "anjo_premium_monthly",
        "expiration_at_ms": 9999999999000,
    })

    status = auth_client.get("/api/billing/status").json()
    assert status["tier"] == "premium"
    assert status["daily_limit"] == 200


# ── Subscription renewal ──────────────────────────────────────────────────────

def test_webhook_renewal(client, monkeypatch, auth_client):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", WEBHOOK_SECRET)

    from anjo.core.db import get_db
    row = get_db().execute("SELECT user_id FROM users WHERE username = 'testuser'").fetchone()
    user_id = row["user_id"]

    r = _post_webhook(client, {
        "type": "RENEWAL",
        "app_user_id": user_id,
        "product_id": "anjo_pro_monthly",
        "expiration_at_ms": 9999999999000,
    })
    assert r.status_code == 200
    assert auth_client.get("/api/billing/status").json()["tier"] == "pro"


# ── Subscription expiration ───────────────────────────────────────────────────

def test_webhook_expiration(client, monkeypatch, auth_client):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", WEBHOOK_SECRET)

    from anjo.core.db import get_db
    row = get_db().execute("SELECT user_id FROM users WHERE username = 'testuser'").fetchone()
    user_id = row["user_id"]

    # Activate first
    _post_webhook(client, {
        "type": "INITIAL_PURCHASE",
        "app_user_id": user_id,
        "product_id": "anjo_pro_monthly",
        "expiration_at_ms": 9999999999000,
    })
    assert auth_client.get("/api/billing/status").json()["subscribed"] is True

    # Then expire
    _post_webhook(client, {
        "type": "EXPIRATION",
        "app_user_id": user_id,
        "product_id": "anjo_pro_monthly",
    })
    status = auth_client.get("/api/billing/status").json()
    assert status["subscribed"] is False
    assert status["tier"] == "free"


# ── Cancellation (still active until period end) ──────────────────────────────

def test_webhook_cancellation_stays_active(client, monkeypatch, auth_client):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", WEBHOOK_SECRET)

    from anjo.core.db import get_db
    row = get_db().execute("SELECT user_id FROM users WHERE username = 'testuser'").fetchone()
    user_id = row["user_id"]

    _post_webhook(client, {
        "type": "INITIAL_PURCHASE",
        "app_user_id": user_id,
        "product_id": "anjo_pro_monthly",
        "expiration_at_ms": 9999999999000,
    })
    _post_webhook(client, {
        "type": "CANCELLATION",
        "app_user_id": user_id,
        "product_id": "anjo_pro_monthly",
    })
    # Cancelled but not expired — should still be subscribed
    assert auth_client.get("/api/billing/status").json()["subscribed"] is True


# ── Credit packs (NON_RENEWING_PURCHASE) ─────────────────────────────────────

def test_webhook_credits_100(client, monkeypatch, auth_client):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", WEBHOOK_SECRET)

    from anjo.core.db import get_db
    row = get_db().execute("SELECT user_id FROM users WHERE username = 'testuser'").fetchone()
    user_id = row["user_id"]

    r = _post_webhook(client, {
        "type": "NON_RENEWING_PURCHASE",
        "app_user_id": user_id,
        "product_id": "anjo_credits_100",
        "transaction_id": "txn_001",
    })
    assert r.status_code == 200
    assert auth_client.get("/api/billing/status").json()["message_credits"] == 100


def test_webhook_credits_idempotent(client, monkeypatch, auth_client):
    """Same transaction_id sent twice must only credit once."""
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", WEBHOOK_SECRET)

    from anjo.core.db import get_db
    row = get_db().execute("SELECT user_id FROM users WHERE username = 'testuser'").fetchone()
    user_id = row["user_id"]

    event = {
        "type": "NON_RENEWING_PURCHASE",
        "app_user_id": user_id,
        "product_id": "anjo_credits_500",
        "transaction_id": "txn_dupe",
    }
    _post_webhook(client, event)
    _post_webhook(client, event)  # duplicate

    assert auth_client.get("/api/billing/status").json()["message_credits"] == 500


def test_webhook_missing_user_id_ignored(client, monkeypatch):
    monkeypatch.setenv("REVENUECAT_WEBHOOK_SECRET", WEBHOOK_SECRET)
    r = _post_webhook(client, {"type": "INITIAL_PURCHASE", "product_id": "anjo_pro_monthly"})
    assert r.status_code == 200
