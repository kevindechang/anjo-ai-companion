"""User facts — concrete persistent details extracted from conversations.

Each fact is stored as a dict:
  {"text": str, "added_at": ISO timestamp, "confidence": float, "superseded_at": ISO | None}

Superseded facts are excluded from prompt injection but retained for reference.
Supersession fires when a new fact shares a semantic category (job, city, relationship
status, education) with an existing active fact — the old one is retired automatically.

Max _MAX_FACTS active (non-superseded) facts at any time.

Backwards-compatible: reads old format (list of plain strings + separate confidence_json).
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import TypedDict

from anjo.core.crypto import decrypt_db, encrypt_db
from anjo.core.db import get_db

_MAX_FACTS = 15
# Keep at most this many superseded facts (prevents unbounded growth)
_MAX_SUPERSEDED = 15


class FactRecord(TypedDict):
    text: str
    added_at: str           # ISO 8601 UTC timestamp
    confidence: float       # 0.0–1.0
    superseded_at: str | None  # ISO timestamp when retired, else None


# ── Category supersession ─────────────────────────────────────────────────────
# When a new fact matches a category that an existing active fact also matches,
# the existing fact is superseded (retired with a timestamp).

_SUPERSEDABLE: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"\b(work|job|career|profession|employ|engineer|developer|doctor|nurse|teacher|"
        r"student|manager|designer|scientist|lawyer|analyst|accountant|architect|chef|"
        r"pilot|therapist|consultant|writer|artist|musician|athlete|programmer|coder|"
        r"intern|freelanc)\b", re.I),
     "occupation"),
    (re.compile(
        r"\b(live|lives|living|reside|based in|moved to|moved from|relocat|settled in|"
        r"in (tokyo|london|new york|paris|berlin|sydney|seoul|singapore|dubai|toronto|"
        r"chicago|los angeles|san francisco|beijing|shanghai|mumbai|delhi))\b", re.I),
     "location"),
    (re.compile(
        r"\b(married|single|divorced|dating|relationship|partner|girlfriend|boyfriend|"
        r"wife|husband|engaged|widowed|separated|broke up|breaking up)\b", re.I),
     "relationship_status"),
    (re.compile(
        r"\b(study|studying|school|university|college|major|degree|graduate|graduated|"
        r"enrolled|enrollment|phd|masters|bachelors)\b", re.I),
     "education"),
]


def _fact_category(text: str) -> str | None:
    """Return the supersedable category this fact belongs to, or None."""
    for pattern, cat in _SUPERSEDABLE:
        if pattern.search(text):
            return cat
    return None


def _sanitize_fact(fact: str) -> str:
    return html.escape(fact).strip()


# ── Internal load/save ────────────────────────────────────────────────────────

def _load_all(user_id: str) -> list[FactRecord]:
    """Load all fact records (active + superseded), handling both old and new formats."""
    row = get_db().execute(
        "SELECT facts_json, confidence_json, updated_at FROM facts WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return []

    fallback_ts = row["updated_at"] or datetime.now(timezone.utc).isoformat()

    try:
        raw = json.loads(decrypt_db(row["facts_json"]))
    except Exception:
        return []

    if not raw:
        return []

    # ── Old format: list of plain strings ────────────────────────────────────
    if isinstance(raw[0], str):
        try:
            confs = json.loads(decrypt_db(row["confidence_json"] or "[]"))
        except Exception:
            confs = []
        records: list[FactRecord] = []
        for i, text in enumerate(raw):
            if not text:
                continue
            conf = float(confs[i]) if i < len(confs) else 1.0
            records.append({
                "text": _sanitize_fact(text),
                "added_at": fallback_ts,
                "confidence": conf,
                "superseded_at": None,
            })
        return records

    # ── New format: list of dicts ─────────────────────────────────────────────
    records = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("text"):
            continue
        records.append({
            "text": _sanitize_fact(item["text"]),
            "added_at": item.get("added_at", fallback_ts),
            "confidence": float(item.get("confidence", 1.0)),
            "superseded_at": item.get("superseded_at"),
        })
    return records


def _save_all(user_id: str, records: list[FactRecord]) -> None:
    """Persist all records (active + superseded) in new format."""
    now = datetime.now(timezone.utc).isoformat()
    data = [
        {
            "text": r["text"],
            "added_at": r["added_at"],
            "confidence": r["confidence"],
            "superseded_at": r["superseded_at"],
        }
        for r in records
    ]
    db = get_db()
    db.execute(
        "INSERT INTO facts (user_id, facts_json, confidence_json, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "facts_json = excluded.facts_json, "
        "confidence_json = excluded.confidence_json, "
        "updated_at = excluded.updated_at",
        (user_id, encrypt_db(json.dumps(data)), encrypt_db("[]"), now),
    )
    db.commit()


# ── Public API ────────────────────────────────────────────────────────────────

def load_facts(user_id: str) -> list[str]:
    """Return active (non-superseded) fact texts, newest first."""
    return [r["text"] for r in _load_all(user_id) if not r["superseded_at"]]


def load_facts_with_confidence(user_id: str) -> list[tuple[str, float]]:
    """Return (text, confidence) pairs for active facts. Backwards-compatible."""
    return [
        (r["text"], r["confidence"])
        for r in _load_all(user_id)
        if not r["superseded_at"]
    ]


def load_facts_with_meta(user_id: str) -> list[FactRecord]:
    """Return full FactRecord dicts for active facts (newest first)."""
    return [r for r in _load_all(user_id) if not r["superseded_at"]]


def merge_facts(
    user_id: str,
    new_facts: list[str],
    confidences: list[float] | None = None,
) -> None:
    """Add new facts, superseding same-category old facts. Cap active facts at _MAX_FACTS.

    confidences: per-fact scores in [0.0, 1.0]. Defaults to 1.0.
    """
    if not new_facts:
        return

    new_facts = [_sanitize_fact(f) for f in new_facts if f]
    if not new_facts:
        return

    if confidences is None:
        confidences = [1.0] * len(new_facts)
    else:
        confidences = [max(0.0, min(1.0, float(c))) for c in confidences]
        if len(confidences) < len(new_facts):
            confidences += [1.0] * (len(new_facts) - len(confidences))
        confidences = confidences[:len(new_facts)]

    now = datetime.now(timezone.utc).isoformat()
    existing = _load_all(user_id)
    active = [r for r in existing if not r["superseded_at"]]
    active_lower = {r["text"].lower() for r in active}

    to_add: list[FactRecord] = []
    supersede_texts: set[str] = set()  # lowercase texts of facts to retire

    for fact, conf in zip(new_facts, confidences):
        if fact.lower() in active_lower:
            continue  # exact duplicate — skip

        # Detect category and supersede any existing fact in the same category
        cat = _fact_category(fact)
        if cat:
            for rec in active:
                if not rec["superseded_at"] and _fact_category(rec["text"]) == cat:
                    supersede_texts.add(rec["text"].lower())

        to_add.append({
            "text": fact,
            "added_at": now,
            "confidence": conf,
            "superseded_at": None,
        })

    # Apply supersession timestamps to existing records
    updated_existing: list[FactRecord] = []
    for r in existing:
        if r["text"].lower() in supersede_texts and not r["superseded_at"]:
            updated_existing.append({**r, "superseded_at": now})
        else:
            updated_existing.append(r)

    # Rebuild: new facts prepended to remaining active facts, capped at _MAX_FACTS
    remaining_active = [r for r in updated_existing if not r["superseded_at"]]
    new_active = (to_add + remaining_active)[:_MAX_FACTS]

    # Retain superseded facts up to _MAX_SUPERSEDED (oldest superseded dropped first)
    superseded = sorted(
        [r for r in updated_existing if r["superseded_at"]],
        key=lambda r: r["superseded_at"] or "",
        reverse=True,
    )[:_MAX_SUPERSEDED]

    _save_all(user_id, new_active + superseded)
