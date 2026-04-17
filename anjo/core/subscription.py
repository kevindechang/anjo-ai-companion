"""Subscription state and free-tier message gating.

Tiers:
    free     — FREE_DAILY_LIMIT messages per day (Standard model after first sessions)
    pro      — PRO_DAILY_LIMIT messages per day  ($12/mo, Advanced model)
    premium  — PREMIUM_DAILY_LIMIT messages per day ($22/mo, Advanced model)

Overflow: any tier can hold message credits that kick in when the daily
limit is exhausted. Credits are bought as one-time packs.

Gate: can_send_message(user_id) — True if within daily limit OR has credits.
Model: get_model_for_user(user_id) — 'sonnet' | 'haiku'
"""
from __future__ import annotations

from datetime import datetime, timezone

from anjo.core.db import get_db

FREE_DAILY_LIMIT    = 20
PRO_DAILY_LIMIT     = 60
PREMIUM_DAILY_LIMIT = 200

FREE_SONNET_SESSIONS = 3  # free users get Sonnet for their first N full sessions


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Subscription status ───────────────────────────────────────────────────────

def get_subscription(user_id: str) -> dict:
    row = get_db().execute(
        "SELECT status, tier, fs_account_id, fs_subscription_id, current_period_end, updated_at "
        "FROM subscriptions WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return {"status": "none"}
    return dict(row)


def is_subscribed(user_id: str) -> bool:
    row = get_db().execute(
        "SELECT status FROM subscriptions WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row is not None and row["status"] == "active"


def get_tier(user_id: str) -> str:
    """Return 'free' | 'pro' | 'premium'."""
    row = get_db().execute(
        "SELECT status, tier FROM subscriptions WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row or row["status"] != "active":
        return "free"
    return row["tier"] or "pro"


def get_daily_limit(user_id: str) -> int:
    return {
        "free":    FREE_DAILY_LIMIT,
        "pro":     PRO_DAILY_LIMIT,
        "premium": PREMIUM_DAILY_LIMIT,
    }.get(get_tier(user_id), FREE_DAILY_LIMIT)


def get_free_sessions_used(user_id: str) -> int:
    """Number of full sessions completed while on the free tier."""
    row = get_db().execute(
        "SELECT free_sessions_used FROM subscriptions WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row["free_sessions_used"] if row else 0


def increment_free_sessions(user_id: str) -> int:
    """Increment completed session count for a free-tier user. No-op for paid users."""
    if get_tier(user_id) != "free":
        return get_free_sessions_used(user_id)
    db = get_db()
    db.execute(
        "INSERT INTO subscriptions (user_id, status, tier, free_sessions_used, updated_at) "
        "VALUES (?, 'none', 'free', 1, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "  free_sessions_used = free_sessions_used + 1, "
        "  updated_at = excluded.updated_at",
        (user_id, _now()),
    )
    db.commit()
    return get_free_sessions_used(user_id)


def get_model_for_user(user_id: str) -> str:
    """Return 'sonnet' | 'haiku' based on tier and session history.

    Pro/Premium always get Sonnet.
    Free users get Sonnet for their first FREE_SONNET_SESSIONS full sessions,
    then fall back to Haiku permanently.
    """
    tier = get_tier(user_id)
    if tier in ("pro", "premium"):
        return "sonnet"
    return "sonnet" if get_free_sessions_used(user_id) < FREE_SONNET_SESSIONS else "haiku"


def set_subscription(
    user_id: str,
    status: str,
    tier: str = "pro",
    fs_account_id: str = "",
    fs_subscription_id: str = "",
    current_period_end: str = "",
) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO subscriptions (user_id, status, tier, fs_account_id, fs_subscription_id, current_period_end, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "  status = excluded.status, "
        "  tier = excluded.tier, "
        "  fs_account_id = CASE WHEN excluded.fs_account_id != '' THEN excluded.fs_account_id ELSE fs_account_id END, "
        "  fs_subscription_id = CASE WHEN excluded.fs_subscription_id != '' THEN excluded.fs_subscription_id ELSE fs_subscription_id END, "
        "  current_period_end = CASE WHEN excluded.current_period_end != '' THEN excluded.current_period_end ELSE current_period_end END, "
        "  updated_at = excluded.updated_at",
        (user_id, status, tier, fs_account_id, fs_subscription_id, current_period_end, _now()),
    )
    db.commit()


# ── Daily usage ───────────────────────────────────────────────────────────────

def get_daily_messages_used(user_id: str) -> int:
    row = get_db().execute(
        "SELECT count FROM daily_usage WHERE user_id = ? AND date = ?",
        (user_id, _today()),
    ).fetchone()
    return row["count"] if row else 0


def get_daily_messages_remaining(user_id: str) -> int:
    return max(0, get_daily_limit(user_id) - get_daily_messages_used(user_id))


def increment_daily_messages(user_id: str) -> int:
    """Increment today's usage counter atomically via SQL UPSERT. Returns new count."""
    db = get_db()
    db.execute(
        "INSERT INTO daily_usage (user_id, date, count) VALUES (?, ?, 1) "
        "ON CONFLICT(user_id, date) DO UPDATE SET count = count + 1",
        (user_id, _today()),
    )
    db.commit()
    return get_daily_messages_used(user_id)


# ── Gate ──────────────────────────────────────────────────────────────────────

def can_send_message(user_id: str) -> bool:
    """True if user may send a message: within daily limit OR has credit overflow."""
    if get_daily_messages_remaining(user_id) > 0:
        return True
    from anjo.core.credits import get_message_credits
    return get_message_credits(user_id) > 0


def deduct_message_count(user_id: str) -> None:
    """Deduct from the best available budget: daily -> credits."""
    if get_daily_messages_remaining(user_id) > 0:
        increment_daily_messages(user_id)
    else:
        from anjo.core.credits import deduct_message_credit
        deduct_message_credit(user_id)
