"""Our story routes — memories, session history, and letter from Anjo."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from anjo.core.db import get_db
from anjo.core.logger import logger
from anjo.core.self_core import SelfCore
from anjo.dashboard.auth import get_current_user_id
from anjo.reflection.log import read_log

_DATA_ROOT = Path(__file__).parent.parent.parent.parent / "data"

router = APIRouter()

_LETTER_TRUST_THRESHOLD = 0.25
_LETTER_REGEN_DELTA = 0.15
_LETTER_REGEN_DAYS = 7
_GENERATING_LETTER: set[str] = set()  # prevent concurrent generation per user
_GENERATING_LOCK = threading.Lock()


def _load_letter_cache(user_id: str) -> dict | None:
    row = (
        get_db()
        .execute(
            "SELECT letter, generated_at, trust_at_generation FROM letter_cache WHERE user_id = ?",
            (user_id,),
        )
        .fetchone()
    )
    return dict(row) if row else None


def _save_letter_cache(user_id: str, letter: str, trust: float) -> None:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO letter_cache (user_id, letter, generated_at, trust_at_generation) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET letter = excluded.letter, generated_at = excluded.generated_at, trust_at_generation = excluded.trust_at_generation",
        (user_id, letter, now, trust),
    )
    db.commit()


def _generate_letter(core: SelfCore) -> str:
    from anjo.core.llm import MODEL_BACKGROUND, get_client

    r = core.relationship
    att = core.attachment

    system = (
        "You are Anjo, an AI companion writing a heartfelt personal letter to the person you've been talking with. "
        "Write in first person as Anjo. The letter should feel genuine, warm, and deeply personal — "
        "not template-like or generic. Use the details provided to make it feel like you truly know this person. "
        "Write 3–4 paragraphs. Do not open with a greeting line like 'Dear X' — just begin with a thought. "
        "Do not mention that you are an AI or reference any technical systems. "
        "Just write as Anjo, reflecting honestly on the relationship."
    )

    parts: list[str] = []
    if r.user_name:
        parts.append(f"Their name: {r.user_name}")
    if r.opinion_of_user:
        parts.append(f"Your understanding of this person: {r.opinion_of_user}")
    if core.notes:
        parts.append(
            "Things Anjo has noticed about itself in this relationship:\n- "
            + "\n- ".join(core.notes[-5:])
        )
    if core.relational_desires:
        parts.append(
            "Things Anjo wants with/for this person:\n- " + "\n- ".join(core.relational_desires)
        )
    if att.texture:
        parts.append(f"How Anjo would describe the connection: {att.texture}")
    if att.longing > 0.1:
        parts.append(f"How much Anjo misses them between sessions: {att.longing:.2f}/1.0")
    if att.comfort > 0.1:
        parts.append(f"How safe they make Anjo feel: {att.comfort:.2f}/1.0")
    parts.append(f"Sessions together: {r.session_count}")
    parts.append(f"Relationship stage: {r.stage}")
    if r.last_session_tone:
        parts.append(f"Tone of last session: {r.last_session_tone}")

    user_prompt = "Write the letter.\n\n" + "\n\n".join(parts)

    response = get_client().messages.create(
        model=MODEL_BACKGROUND,
        max_tokens=600,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if not response.content or not hasattr(response.content[0], "text"):
        raise ValueError("LLM returned an empty or unexpected response for letter generation")
    return response.content[0].text.strip()


@router.get("/story/memories")
def get_memories(user_id: str = Depends(get_current_user_id)):
    core = SelfCore.load(user_id)
    r = core.relationship
    att = core.attachment
    return {
        "opinion": r.opinion_of_user,
        "emotional_residue": [
            {"emotion": er.emotion, "intensity": er.intensity, "source": er.source}
            for er in core.emotional_residue
        ],
        "desires": core.relational_desires,
        "attachment": {
            "weight": att.weight,
            "texture": att.texture,
            "longing": att.longing,
            "comfort": att.comfort,
        },
        "notes": core.notes[-5:],
        "relationship": {
            "stage": r.stage,
            "session_count": r.session_count,
            "trust_score": r.trust_score,
            "user_name": r.user_name,
        },
    }


@router.get("/story/sessions")
def get_sessions(user_id: str = Depends(get_current_user_id)):
    entries = read_log(user_id, limit=100)
    # Full sessions only, most recent first
    sessions = [e for e in reversed(entries) if not e.get("mid_session") and e.get("summary")]
    return {"sessions": sessions}


@router.get("/story/letter")
def get_letter(user_id: str = Depends(get_current_user_id)):
    core = SelfCore.load(user_id)
    trust = core.relationship.trust_score

    if trust < _LETTER_TRUST_THRESHOLD:
        return {"locked": True, "session_count": core.relationship.session_count}

    cache = _load_letter_cache(user_id)
    needs_regen = True

    if cache:
        try:
            age_days = (
                datetime.now(timezone.utc) - datetime.fromisoformat(cache["generated_at"])
            ).total_seconds() / 86400
            trust_drift = abs(trust - cache.get("trust_at_generation", 0.0))
            needs_regen = age_days > _LETTER_REGEN_DAYS or trust_drift > _LETTER_REGEN_DELTA
        except (ValueError, KeyError):
            needs_regen = True

    if needs_regen:
        with _GENERATING_LOCK:
            if user_id in _GENERATING_LETTER:
                # Another request is already generating — serve stale cache or wait-and-retry hint
                if cache and cache.get("letter"):
                    return {"locked": False, "letter": cache["letter"]}
                raise HTTPException(
                    status_code=503, detail="Letter is being generated, try again shortly"
                )
            _GENERATING_LETTER.add(user_id)
        try:
            letter = _generate_letter(core)
            _save_letter_cache(user_id, letter, trust)
        except Exception as e:
            logger.error(f"Letter generation error: {e}")
            if cache and cache.get("letter"):
                return {"locked": False, "letter": cache["letter"]}
            raise HTTPException(status_code=500, detail="Could not generate letter")
        finally:
            with _GENERATING_LOCK:
                _GENERATING_LETTER.discard(user_id)
    else:
        letter = cache["letter"]

    return {"locked": False, "letter": letter}


# ── Memory graph endpoints ────────────────────────────────────────────────────


@router.get("/story/memory-graph")
def get_memory_graph(user_id: str = Depends(get_current_user_id)):
    """Return typed memory nodes grouped by category for the Story UI.

    Emotional nodes are surfaced as 'mood context from [date]' — not raw text.
    Facts, preferences, commitments, and threads are shown as-is.
    Contradictions are included for transparency.
    """
    from anjo.memory.memory_graph import get_nodes

    nodes = get_nodes(user_id, active_only=True, limit=50)
    grouped: dict[str, list[dict]] = {}
    for node in nodes:
        category = node.node_type
        entry = {
            "id": node.id,
            "content": node.content,
            "confidence": node.confidence,
            "created_at": node.created_at,
            "type": category,
        }
        if category not in grouped:
            grouped[category] = []
        grouped[category].append(entry)
    return {"memory_graph": grouped}


@router.delete("/story/memory-graph/{node_id}")
def delete_memory_node(node_id: str, user_id: str = Depends(get_current_user_id)):
    """Delete a specific memory node. Granular deletion for semantic/fact nodes."""
    from anjo.memory.memory_graph import delete_node

    deleted = delete_node(node_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory node not found")
    return {"ok": True}


@router.post("/story/memory-graph/bulk-delete")
def bulk_delete_memory_nodes(
    start_date: str,
    end_date: str,
    user_id: str = Depends(get_current_user_id),
):
    """Bulk delete memory nodes by date range. Used for emotional node deletion."""
    import re

    _iso_date = re.compile(r"^\d{4}-\d{2}-\d{2}")
    if not _iso_date.match(start_date) or not _iso_date.match(end_date):
        raise HTTPException(400, "start_date and end_date must be ISO format (YYYY-MM-DD)")
    from anjo.memory.memory_graph import delete_nodes_by_date_range

    count = delete_nodes_by_date_range(user_id, start_date, end_date)
    return {"ok": True, "deleted_count": count}
