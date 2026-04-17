"""Reflection log — per-user append-only JSONL record of every reflection run."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from anjo.core.crypto import read_encrypted, write_encrypted

_DATA_ROOT = Path(__file__).parent.parent.parent / "data"


def _log_path(user_id: str) -> Path:
    return _DATA_ROOT / "users" / user_id / "reflection_log.jsonl"


def append_log(
    session_id: str,
    deltas: dict,
    memory_data: dict,
    message_count: int,
    user_id: str,
    mid_session: bool = False,
    triggers: list | None = None,
    valence: float | None = None,
) -> None:
    path = _log_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "mid_session": mid_session,
        "message_count": message_count,
        "deltas": deltas,
        "valence": valence,
        "triggers": triggers or [],
        "significance": memory_data.get("significance"),
        "emotional_tone": memory_data.get("emotional_tone"),
        "emotional_valence": memory_data.get("emotional_valence"),
        "topics": memory_data.get("topics", []),
        "summary": memory_data.get("summary"),
        "opinion_update": memory_data.get("opinion_update"),
        "note": memory_data.get("note"),
    }
    # AES-GCM cannot be appended to — read existing lines, append, rewrite encrypted.
    existing = ""
    if path.exists():
        try:
            existing = read_encrypted(path)
        except Exception:
            existing = ""
    updated = existing + json.dumps(entry) + "\n"
    path.write_bytes(write_encrypted(updated))


def read_log(user_id: str, limit: int = 50) -> list[dict]:
    path = _log_path(user_id)
    if not path.exists():
        return []
    entries = []
    try:
        text = read_encrypted(path)
    except Exception:
        return []
    for line in text.splitlines():
        try:
            entries.append(json.loads(line))
        except Exception:
            pass
    return entries[-limit:]
