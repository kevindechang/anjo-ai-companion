"""Per-user chat history — permanently stored in SQLite."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from anjo.core.crypto import decrypt_db, encrypt_db

_DATA_ROOT = Path(__file__).parent.parent.parent / "data"


def append_message(user_id: str, role: str, content: str) -> None:
    from anjo.core.db import get_db
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO messages (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (user_id, role, encrypt_db(content), now),
    )
    db.commit()


def get_history(user_id: str, limit: int = 500) -> list[dict]:
    from anjo.core.db import get_db
    rows = get_db().execute(
        "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return [{"role": r["role"], "content": decrypt_db(r["content"])} for r in reversed(rows)]


def get_last_n(user_id: str, n: int = 6) -> list[dict]:
    """Return the last n messages for seeding a new session with prior context."""
    from anjo.core.db import get_db
    rows = get_db().execute(
        "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, n),
    ).fetchall()
    return [{"role": r["role"], "content": decrypt_db(r["content"])} for r in reversed(rows)]


def has_any_messages(user_id: str) -> bool:
    from anjo.core.db import get_db
    row = get_db().execute(
        "SELECT 1 FROM messages WHERE user_id = ? LIMIT 1", (user_id,)
    ).fetchone()
    return row is not None


def clear(user_id: str) -> None:
    """Delete all stored messages for this user (factory reset)."""
    from anjo.core.db import get_db
    db = get_db()
    db.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
    db.commit()
    # Also remove any leftover legacy JSONL file
    (_DATA_ROOT / "users" / user_id / "chat_history.jsonl").unlink(missing_ok=True)
