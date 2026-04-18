"""Pending transcript queue — per-user, reflected on next startup if missed.

Flow:
  1. Session ends → save_pending() writes transcript to data/users/{user_id}/pending/{session_id}.json
  2. Next startup → process_all_pending() picks up any saved transcripts and reflects on them
  3. Successful reflection → pending file deleted
  4. If reflection fails → file stays, retried next startup
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from anjo.core.crypto import read_encrypted, write_encrypted
from anjo.core.logger import logger

_DATA_ROOT = Path(__file__).parent.parent.parent / "data"


def _pending_dir(user_id: str) -> Path:
    return _DATA_ROOT / "users" / user_id / "pending"


def save_pending(transcript: list[dict], user_id: str, session_id: str) -> Path:
    """Write transcript to disk immediately. No LLM involved — always fast."""
    pending_dir = _pending_dir(user_id)
    pending_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "transcript": transcript,
        "user_id": user_id,
        "session_id": session_id,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    path = pending_dir / f"{session_id}.json"
    path.write_bytes(write_encrypted(json.dumps(data, indent=2)))
    return path


def delete_pending(path: str | Path) -> None:
    Path(path).unlink(missing_ok=True)


def process_all_pending() -> int:
    """Reflect on pending transcripts for all users. Returns number processed."""
    from anjo.core.self_core import SelfCore
    from anjo.reflection.engine import run_reflection

    users_dir = _DATA_ROOT / "users"
    if not users_dir.exists():
        return 0

    total = 0
    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir():
            continue
        user_id = user_dir.name
        pending_dir = user_dir / "pending"
        if not pending_dir.exists():
            continue
        core = SelfCore.load(user_id)
        for path in sorted(pending_dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
            try:
                data = json.loads(read_encrypted(path))
                run_reflection(
                    transcript=data["transcript"],
                    core=core,
                    user_id=user_id,
                    session_id=data["session_id"],
                )
                path.unlink(missing_ok=True)
                total += 1
            except Exception as e:
                logger.error(f"Could not reflect on pending {path.name}: {e}")

    return total
