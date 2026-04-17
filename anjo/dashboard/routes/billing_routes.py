"""Billing routes — RevenueCat (iOS/mobile)."""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from anjo.dashboard.auth import get_current_user_id
from anjo.core.logger import logger
from anjo.core.subscription import (
    get_daily_limit,
    get_daily_messages_remaining,
    get_daily_messages_used,
    get_subscription,
    get_tier,
    is_subscribed,
    set_subscription,
)
from anjo.core.credits import get_message_credits, add_message_credits

router = APIRouter()

PAYMENTS_ENABLED = True

# RevenueCat product IDs → tier (subscriptions)
_RC_PRODUCT_TIERS: dict[str, str] = {
    "anjo_pro_monthly":     "pro",
    "anjo_pro_annual":      "pro",
    "anjo_premium_monthly": "premium",
    "anjo_premium_annual":  "premium",
}

# RevenueCat product IDs → credit count (one-time purchases)
_RC_CREDIT_PRODUCTS: dict[str, int] = {
    "anjo_credits_100":  100,
    "anjo_credits_500":  500,
    "anjo_credits_1000": 1000,
}

# Product catalogue returned to clients
_RC_PRODUCTS: dict[str, dict] = {
    "pro_monthly":     {"product_id": "anjo_pro_monthly",     "tier": "pro",     "interval": "monthly"},
    "pro_annual":      {"product_id": "anjo_pro_annual",      "tier": "pro",     "interval": "annual"},
    "premium_monthly": {"product_id": "anjo_premium_monthly", "tier": "premium", "interval": "monthly"},
    "premium_annual":  {"product_id": "anjo_premium_annual",  "tier": "premium", "interval": "annual"},
    "credits_100":     {"product_id": "anjo_credits_100",     "type": "credits", "amount": 100},
    "credits_500":     {"product_id": "anjo_credits_500",     "type": "credits", "amount": 500},
    "credits_1000":    {"product_id": "anjo_credits_1000",    "type": "credits", "amount": 1000},
}


# ── Status & config ───────────────────────────────────────────────────────────

@router.get("/billing/status")
def billing_status(user_id: str = Depends(get_current_user_id)):
    tier = get_tier(user_id)
    sub  = get_subscription(user_id)
    return {
        "tier":               tier,
        "subscribed":         is_subscribed(user_id),
        "daily_limit":        get_daily_limit(user_id),
        "messages_used":      get_daily_messages_used(user_id),
        "messages_remaining": get_daily_messages_remaining(user_id),
        "message_credits":    get_message_credits(user_id),
        "period_end":         sub.get("current_period_end", ""),
        "payments_enabled":   PAYMENTS_ENABLED,
    }


@router.get("/billing/config")
def billing_config(user_id: str = Depends(get_current_user_id)):
    return {
        "payments_enabled": PAYMENTS_ENABLED,
        "user_id":          user_id,
        "products":         _RC_PRODUCTS if PAYMENTS_ENABLED else {},
    }


# ── RevenueCat webhook ────────────────────────────────────────────────────────

@router.post("/billing/webhook")
async def revenuecat_webhook(request: Request):
    auth   = request.headers.get("Authorization", "")
    secret = os.environ.get("REVENUECAT_WEBHOOK_SECRET", "")

    if not secret:
        raise HTTPException(500, "RevenueCat webhook secret not configured")
    if not hmac.compare_digest(auth, f"Bearer {secret}"):
        raise HTTPException(401, "Invalid webhook secret")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    event      = body.get("event", {})
    etype      = event.get("type", "")
    user_id    = event.get("app_user_id", "")
    product_id = event.get("product_id", "")

    if not user_id:
        return {"ok": True}

    if etype in ("INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE", "UNCANCELLATION"):
        tier      = _RC_PRODUCT_TIERS.get(product_id, "pro")
        exp_ms    = event.get("expiration_at_ms")
        period_end = (
            datetime.fromtimestamp(exp_ms / 1000, tz=timezone.utc).isoformat()
            if exp_ms else ""
        )
        set_subscription(user_id, status="active", tier=tier, current_period_end=period_end)
        logger.info(f"RC {etype}: {tier} for {user_id}")

    elif etype == "EXPIRATION":
        set_subscription(user_id, status="cancelled")
        logger.info(f"RC EXPIRATION for {user_id}")

    elif etype == "NON_RENEWING_PURCHASE":
        n = _RC_CREDIT_PRODUCTS.get(product_id, 0)
        if n <= 0:
            return {"ok": True}
        transaction_id = (event.get("transaction_id") or event.get("original_transaction_id") or "").strip()
        event_eid = str(event.get("id") or "").strip()
        dedupe_id = transaction_id or event_eid
        if not dedupe_id:
            ts = event.get("purchased_at_ms") or event.get("event_timestamp_ms") or ""
            dedupe_id = "rc_nr:" + hashlib.sha256(
                f"{user_id}:{product_id}:{n}:{ts}".encode()
            ).hexdigest()[:48]

        from anjo.core.db import get_db

        db = get_db()
        try:
            db.execute(
                "INSERT INTO processed_transactions (transaction_id, user_id, processed_at) "
                "VALUES (?, ?, ?)",
                (dedupe_id, user_id, datetime.now(timezone.utc).isoformat()),
            )
            db.commit()
        except Exception:
            logger.info(f"Duplicate RC credit purchase ignored: {dedupe_id}")
            return {"ok": True}

        total = add_message_credits(user_id, n)
        logger.info(f"RC credits: +{n} for {user_id} → total {total}")

    return {"ok": True}
