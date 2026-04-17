"""Credit system — tracks per-user API spend and balance.

New users receive INITIAL_CREDIT_USD on registration.
Each API call deducts actual cost. When balance hits zero, Anjo
says so once and the stream is blocked until the user tops up.
"""
from __future__ import annotations

from datetime import datetime, timezone

from anjo.core.db import get_db

INITIAL_CREDIT_USD: float = 5.00

# Anthropic pricing (USD per token)
_SONNET_IN  = 3.00  / 1_000_000
_SONNET_OUT = 15.00 / 1_000_000
_HAIKU_IN   = 0.25  / 1_000_000
_HAIKU_OUT  = 1.25  / 1_000_000

_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6":                 (_SONNET_IN,  _SONNET_OUT),
    "claude-haiku-4-5-20251001":         (_HAIKU_IN,   _HAIKU_OUT),
    # Bedrock model names
    "us.anthropic.claude-sonnet-4-6":    (_SONNET_IN,  _SONNET_OUT),
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": (_HAIKU_IN, _HAIKU_OUT),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_row(user_id: str) -> None:
    """Insert a default credits row if one doesn't exist yet."""
    get_db().execute(
        "INSERT OR IGNORE INTO credits (user_id) VALUES (?)", (user_id,)
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def get_balance(user_id: str) -> float:
    row = get_db().execute(
        "SELECT balance_usd FROM credits WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row["balance_usd"] if row else 0.0


def has_balance(user_id: str) -> bool:
    return get_balance(user_id) > 0.0


def grant_initial_credits(user_id: str) -> None:
    """Called on registration. Idempotent — only grants once."""
    db = get_db()
    _ensure_row(user_id)
    db.execute(
        "UPDATE credits SET balance_usd = ?, total_topped_up_usd = ?, last_updated = ? "
        "WHERE user_id = ? AND total_topped_up_usd = 0",
        (INITIAL_CREDIT_USD, INITIAL_CREDIT_USD, _now(), user_id),
    )
    db.commit()


def add_credits(user_id: str, amount_usd: float) -> float:
    """Add credits after a payment. Returns new balance."""
    db = get_db()
    _ensure_row(user_id)
    db.execute(
        "UPDATE credits "
        "SET balance_usd = ROUND(balance_usd + ?, 6), "
        "    total_topped_up_usd = ROUND(total_topped_up_usd + ?, 6), "
        "    last_updated = ? "
        "WHERE user_id = ?",
        (amount_usd, amount_usd, _now(), user_id),
    )
    db.commit()
    return get_balance(user_id)


def deduct_cost(user_id: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """Deduct actual API cost atomically. Returns remaining balance."""
    in_rate, out_rate = _MODEL_PRICING.get(model, (_SONNET_IN, _SONNET_OUT))
    cost = round(input_tokens * in_rate + output_tokens * out_rate, 8)
    db = get_db()
    _ensure_row(user_id)
    # Atomic update: use CASE to ensure balance never goes negative in a single statement
    db.execute(
        "UPDATE credits "
        "SET balance_usd = CASE WHEN balance_usd >= ? THEN ROUND(balance_usd - ?, 6) ELSE 0.0 END, "
        "    total_spent_usd = ROUND(total_spent_usd + ?, 6), "
        "    last_updated = ? "
        "WHERE user_id = ?",
        (cost, cost, cost, _now(), user_id),
    )
    db.commit()
    return get_balance(user_id)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = _MODEL_PRICING.get(model, (_SONNET_IN, _SONNET_OUT))
    return round(input_tokens * in_rate + output_tokens * out_rate, 8)


# ── Message credits (overflow packs) ─────────────────────────────────────────

def get_message_credits(user_id: str) -> int:
    row = get_db().execute(
        "SELECT message_credits FROM credits WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row["message_credits"] if row else 0


def add_message_credits(user_id: str, n: int) -> int:
    """Add n message credits. Returns new total."""
    db = get_db()
    _ensure_row(user_id)
    db.execute(
        "UPDATE credits SET message_credits = message_credits + ?, last_updated = ? WHERE user_id = ?",
        (n, _now(), user_id),
    )
    db.commit()
    return get_message_credits(user_id)


def deduct_message_credit(user_id: str) -> bool:
    """Deduct one message credit atomically. Returns True if successful."""
    db = get_db()
    cursor = db.execute(
        "UPDATE credits SET message_credits = message_credits - 1, last_updated = ? "
        "WHERE user_id = ? AND message_credits > 0",
        (_now(), user_id),
    )
    db.commit()
    return cursor.rowcount > 0


from typing import Any, Callable

def deduct_and_refund_on_error(user_id: str, action: Callable[[], Any]) -> Any:
    """Pre-deduct a message credit, execute action, refund if action raises exception.

    Returns the result of action() if successful. If action() raises an exception,
    the credit is refunded.

    For message credits used in chat flow:
    - Pre-deduct before LLM call
    - If client disconnects or LLM errors, credit is refunded
    - Only finalize the deduction on successful response completion
    """
    db = get_db()
    # Pre-deduct atomically
    db.execute(
        "UPDATE credits SET message_credits = message_credits - 1, last_updated = ? "
        "WHERE user_id = ? AND message_credits > 0",
        (_now(), user_id),
    )
    db.commit()
    row = db.execute("SELECT changes()").fetchone()
    deducted = row[0] > 0 if row is not None else False

    if not deducted:
        # No credit to deduct, restore any negative balance and re-raise
        db.execute(
            "UPDATE credits SET message_credits = message_credits + 1 WHERE user_id = ? AND message_credits < 0",
            (user_id,),
        )
        db.commit()
        raise ValueError("No message credits available")

    try:
        result = action()
        # Success — keep the deduction (already committed)
        return result
    except Exception:
        # Failure — refund the credit
        db.execute(
            "UPDATE credits SET message_credits = message_credits + 1, last_updated = ? WHERE user_id = ?",
            (_now(), user_id),
        )
        db.commit()
        raise
