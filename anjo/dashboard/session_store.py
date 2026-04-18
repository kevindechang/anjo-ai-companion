"""Durable session store — SQLite-backed with in-memory cache.

Sessions are primarily stored in the `active_sessions` SQLite table, with an
in-memory cache for fast access during a session. This means sessions survive
server restarts natively — no more reliance on active_session.json as the
primary recovery mechanism.

On startup, any rows in `active_sessions` are recovered automatically.
"""

from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from anjo.core.crypto import read_encrypted, write_encrypted

_sessions: dict[str, dict[str, Any]] = {}
_sessions_lock = threading.Lock()

INACTIVITY_SECONDS = 10 * 60  # 10 minutes
_DATA_ROOT = Path(__file__).parent.parent.parent / "data"


def _active_session_path(user_id: str) -> Path:
    return _DATA_ROOT / "users" / user_id / "active_session.json"


# ── SQLite persistence ─────────────────────────────────────────────────────────


def _persist_to_db(user_id: str) -> None:
    """Write session state to SQLite active_sessions table."""
    # Copy data under lock; serialize and write outside to reduce contention
    with _sessions_lock:
        session = _sessions.get(user_id)
        if not session:
            return
        state_snapshot = copy.deepcopy(session["state"])
        session_id = session["session_id"]
        last_activity = session["last_activity"]
    state_json = json.dumps(state_snapshot)
    try:
        from datetime import datetime, timezone

        from anjo.core.db import get_db

        db = get_db()
        db.execute(
            "INSERT INTO active_sessions (user_id, session_id, state_json, last_activity, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "state_json = excluded.state_json, last_activity = excluded.last_activity",
            (
                user_id,
                session_id,
                state_json,
                last_activity,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        db.commit()
    except Exception:
        # Fallback to file-based persistence if DB fails
        _persist_session_file(user_id)


def _delete_from_db(user_id: str) -> None:
    """Remove session from SQLite."""
    try:
        from anjo.core.db import get_db

        db = get_db()
        db.execute("DELETE FROM active_sessions WHERE user_id = ?", (user_id,))
        db.commit()
    except Exception:
        pass


def _recover_from_db() -> dict[str, dict]:
    """Recover all sessions from SQLite on startup. Returns {user_id: session_dict}."""
    recovered = {}
    try:
        from anjo.core.db import get_db

        db = get_db()
        rows = db.execute(
            "SELECT user_id, session_id, state_json, last_activity FROM active_sessions"
        ).fetchall()
        for row in rows:
            try:
                state = json.loads(row["state_json"])
                recovered[row["user_id"]] = {
                    "state": state,
                    "session_id": row["session_id"],
                    "last_activity": row["last_activity"],
                    "user_id": row["user_id"],
                }
            except (json.JSONDecodeError, KeyError):
                continue
    except Exception:
        pass
    return recovered


# ── Legacy file persistence (fallback) ─────────────────────────────────────────


def _persist_session_file(user_id: str) -> None:
    # Copy data under lock; file I/O outside to reduce contention
    with _sessions_lock:
        session = _sessions.get(user_id)
        if not session:
            return
        data = {
            "conversation_history": session["state"]["conversation_history"],
            "seed_len": session["state"].get("seed_len", 0),
        }
    path = _active_session_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(write_encrypted(json.dumps(data, indent=2)))


def _load_persisted_session(user_id: str) -> tuple[list[dict], int]:
    """Return (conversation_history, seed_len) from persisted file.

    Handles both legacy format (plain list) and new format (dict with _seed_len).
    """
    path = _active_session_path(user_id)
    if not path.exists():
        return [], 0
    try:
        raw = json.loads(read_encrypted(path))
        if isinstance(raw, list):
            # Legacy format: just the history array, all messages are seed
            return raw, len(raw)
        # New format: dict with conversation_history and _seed_len
        history = raw.get("conversation_history", [])
        seed_len = raw.get("seed_len", len(history))
        return history if isinstance(history, list) else [], seed_len
    except Exception:
        return [], 0


def _delete_persisted_session(user_id: str) -> None:
    _active_session_path(user_id).unlink(missing_ok=True)


# ── Session lifecycle ──────────────────────────────────────────────────────────


def recover_sessions_on_startup() -> int:
    """Recover sessions from SQLite on server startup. Returns count recovered."""
    recovered = _recover_from_db()
    count = 0
    with _sessions_lock:
        for user_id, session_data in recovered.items():
            if user_id not in _sessions:
                from anjo.core.self_core import SelfCore

                core = SelfCore.load(user_id)
                state = session_data["state"]
                state["self_core"] = core.model_dump()
                _sessions[user_id] = {
                    "state": state,
                    "core": core,
                    "user_id": user_id,
                    "session_id": session_data["session_id"],
                    "last_activity": session_data["last_activity"],
                }
                count += 1
    return count


def get_or_create_session(user_id: str) -> str:
    """Return user_id (the session key), creating the session if needed.

    Lock discipline: the global _sessions_lock is held ONLY during dict
    mutation (fast).  All I/O — SelfCore.load, outreach check, LLM first-
    message generation — runs OUTSIDE the lock so one user's session
    creation never blocks another's.
    """
    # Fast path: session already exists
    with _sessions_lock:
        if user_id in _sessions:
            return user_id

    # ── Slow path: load all data OUTSIDE the lock ─────────────────────────
    from anjo.core.outreach import get_pending_outreach, mark_delivered
    from anjo.core.self_core import SelfCore

    core = SelfCore.load(user_id)
    persisted, persisted_seed_len = _load_persisted_session(user_id)
    outreach = get_pending_outreach(user_id)
    cached_facts = _load_user_facts(user_id)
    cached_trends = _load_trending_topics()

    # Seed from last session if no persisted session
    seed: list[dict] = []
    if not persisted:
        from anjo.core.history import get_last_n

        seed = get_last_n(user_id, n=6)

    # First message generation (LLM call — the expensive part, must be outside lock)
    first_msg: str | None = None
    if core.relationship.session_count == 0 and not persisted and not outreach:
        from anjo.core.history import has_any_messages

        if not has_any_messages(user_id):
            from anjo.core.outreach import generate_first_message

            first_msg = generate_first_message()
            if not first_msg:
                first_msg = "Hey — good to meet you. What's going on today?"

    # ── Acquire lock and apply atomically (fast dict ops only) ────────────
    with _sessions_lock:
        # Double-check: another thread may have created the session while we loaded
        if user_id in _sessions:
            return user_id

        _create_session(
            core.model_dump(), core, user_id, cached_facts=cached_facts, cached_trends=cached_trends
        )

        # Restore persisted conversation (with correct seed_len)
        if persisted:
            _sessions[user_id]["state"]["conversation_history"] = persisted
            _sessions[user_id]["state"]["seed_len"] = persisted_seed_len
        elif seed:
            _sessions[user_id]["state"]["conversation_history"] = seed
            _sessions[user_id]["state"]["seed_len"] = len(seed)

        # Inject pending outreach or first message
        if outreach:
            _sessions[user_id]["state"]["conversation_history"].append(
                {"role": "assistant", "content": outreach}
            )
            _sessions[user_id]["state"]["seed_len"] = len(
                _sessions[user_id]["state"]["conversation_history"]
            )
            _sessions[user_id]["pending_outreach"] = outreach
        elif first_msg:
            _sessions[user_id]["state"]["conversation_history"].append(
                {"role": "assistant", "content": first_msg}
            )
            _sessions[user_id]["state"]["seed_len"] = len(
                _sessions[user_id]["state"]["conversation_history"]
            )
            _sessions[user_id]["pending_outreach"] = first_msg

    # ── Side effects outside lock (I/O) ───────────────────────────────────
    if outreach:
        mark_delivered(user_id)
        from anjo.core.history import append_message as _append

        _append(user_id, "assistant", outreach)
    elif first_msg:
        from anjo.core.history import append_message as _append

        _append(user_id, "assistant", first_msg)

    return user_id


def _load_user_facts(user_id: str) -> list[str]:
    try:
        from anjo.core.facts import load_facts

        return load_facts(user_id)
    except Exception:
        return []


def _load_trending_topics() -> list[str]:
    try:
        from datetime import datetime, timedelta, timezone

        from anjo.core.db import get_db

        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        rows = (
            get_db()
            .execute(
                "SELECT topic, COUNT(*) as cnt FROM topic_trends WHERE ts > ? "
                "GROUP BY topic ORDER BY cnt DESC LIMIT 3",
                (cutoff,),
            )
            .fetchall()
        )
        return [row["topic"] for row in rows]
    except Exception:
        return []


def _create_session(
    core_dump: dict,
    core_instance: Any,
    user_id: str,
    *,
    cached_facts: list[str] | None = None,
    cached_trends: list[str] | None = None,
) -> None:
    state: dict = {
        "user_message": "",
        "conversation_history": [],
        "self_core": core_dump,
        "should_retrieve": False,
        "retrieved_memories": [],
        "assistant_response": "",
        "session_tokens": {"input": 0, "output": 0},
        "active_emotions": {},
        "intent": "",
        "occ_carry": {},
        "user_id": user_id,
        "session_id": "",
        "cached_user_facts": cached_facts if cached_facts is not None else [],
        "cached_trending_topics": cached_trends if cached_trends is not None else [],
    }
    _sessions[user_id] = {
        "state": state,
        "core": core_instance,
        "user_id": user_id,
        "session_id": str(uuid.uuid4())[:8],
        "last_activity": time.time(),
    }


def accumulate_tokens(user_id: str, input_tokens: int, output_tokens: int) -> None:
    with _sessions_lock:
        if user_id in _sessions:
            t = _sessions[user_id]["state"]["session_tokens"]
            t["input"] += input_tokens
            t["output"] += output_tokens


def get_session(user_id: str) -> dict[str, Any] | None:
    """Return the live session dict for *user_id*, or ``None``.

    .. warning::

        The returned dict is a **direct mutable reference** to the
        in-memory session.  Callers that only need to *read* session
        state should prefer :func:`get_session_snapshot` (returns a
        deep copy) or :func:`get_self_core_safe`.  Callers that need
        to *write* should do so under ``_sessions_lock`` or use
        :func:`update_session_state` / :func:`set_session_core`.
    """
    return _sessions.get(user_id)


def update_session_state(user_id: str, state: dict) -> None:
    with _sessions_lock:
        if user_id not in _sessions:
            return
        _sessions[user_id]["state"] = state
    # Persist to both SQLite and file (dual-write for safety)
    _persist_to_db(user_id)


def touch_session(user_id: str) -> None:
    with _sessions_lock:
        if user_id in _sessions:
            _sessions[user_id]["last_activity"] = time.time()


def get_inactive_sessions() -> list[dict[str, Any]]:
    now = time.time()
    result = []
    with _sessions_lock:
        for s in _sessions.values():
            if (now - s.get("last_activity", now)) >= INACTIVITY_SECONDS:
                history = s["state"].get("conversation_history") or []
                seed_len = s["state"].get("seed_len", 0)
                new_messages = len(history) - seed_len
                if new_messages > 0:
                    result.append(copy.deepcopy(s))
    return result


def reset_session(user_id: str) -> None:
    """Clear conversation history for the next chat while keeping the session alive."""
    with _sessions_lock:
        if user_id not in _sessions:
            return
    # Load SelfCore OUTSIDE lock — file I/O should never block other sessions
    from anjo.core.self_core import SelfCore

    core = SelfCore.load(user_id)
    with _sessions_lock:
        if user_id not in _sessions:
            return
        _sessions[user_id]["core"] = core
        _sessions[user_id]["state"]["conversation_history"] = []
        _sessions[user_id]["state"]["self_core"] = core.model_dump()
        _sessions[user_id]["state"]["occ_carry"] = {}
        _sessions[user_id]["last_activity"] = time.time()
    _delete_persisted_session(user_id)
    _delete_from_db(user_id)


def refresh_cached_facts(user_id: str) -> None:
    """Refresh cached_user_facts from SQLite."""
    with _sessions_lock:
        if user_id not in _sessions:
            return
    # Load facts OUTSIDE lock — DB I/O should never block other sessions
    facts = _load_user_facts(user_id)
    with _sessions_lock:
        if user_id not in _sessions:
            return
        _sessions[user_id]["state"]["cached_user_facts"] = facts


def get_self_core_safe(user_id: str) -> dict | None:
    """Return a deep copy of the current self_core state under the sessions lock.

    The self_core dict contains nested dicts (mood, personality,
    relationship, attachment, goals, emotional_residue), so a shallow
    ``dict()`` copy would share mutable references with the live
    session.  We use ``copy.deepcopy`` to prevent mutation bleed-through.
    """
    with _sessions_lock:
        session = _sessions.get(user_id)
        if not session:
            return None
        return copy.deepcopy(session["state"]["self_core"])


def set_session_core(user_id: str, core: Any) -> None:
    """Safely update the live SelfCore instance under the sessions lock."""
    with _sessions_lock:
        session = _sessions.get(user_id)
        if session:
            session["core"] = core
            session["state"]["self_core"] = core.model_dump()


def delete_session(user_id: str) -> None:
    with _sessions_lock:
        _sessions.pop(user_id, None)
    _delete_persisted_session(user_id)
    _delete_from_db(user_id)


# ── Public read-only session introspection ──────────────────────────────────


def get_active_session_count() -> int:
    """Return the number of currently active in-memory sessions."""
    return len(_sessions)


def get_session_status(user_id: str) -> tuple[bool, float | None]:
    """Return (is_active, last_activity_timestamp) for a user."""
    with _sessions_lock:
        session = _sessions.get(user_id)
    if session is None:
        return False, None
    return True, session.get("last_activity")


def get_session_snapshot(user_id: str) -> dict[str, Any] | None:
    """Return a deep copy of the session — safe for read-only use.

    Use this instead of get_session() when callers do NOT need to
    mutate the live session state.
    """
    with _sessions_lock:
        session = _sessions.get(user_id)
        return copy.deepcopy(session) if session else None


def check_and_cleanup_session(user_id: str, stale_before: float) -> bool:
    """Atomically check if session is stale and clean up.

    Returns True if session was removed (or didn't exist),
    False if session has new activity and core was refreshed.
    """
    with _sessions_lock:
        session = _sessions.get(user_id)
        if not session:
            return True
        is_stale = session.get("last_activity", 0) <= stale_before
        if is_stale:
            _sessions.pop(user_id, None)

    if is_stale:
        _delete_persisted_session(user_id)
        _delete_from_db(user_id)
        return True

    # Session still active — refresh core from disk
    from anjo.core.self_core import SelfCore

    fresh = SelfCore.load(user_id)
    with _sessions_lock:
        sess = _sessions.get(user_id)
        if sess:
            sess["core"] = fresh
            sess["state"]["self_core"] = fresh.model_dump()
    return False
