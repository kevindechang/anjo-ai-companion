"""Factory reset endpoint — wipes all persistent state for the current user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from anjo.core.logger import logger
from anjo.dashboard.auth import get_current_user_id, verify_password

router = APIRouter()


@router.post("/reset")
async def factory_reset(request: Request, user_id: str = Depends(get_current_user_id)):
    body = await request.json()
    password = body.get("password", "")
    if not verify_password(user_id, password):
        raise HTTPException(403, "Incorrect password.")
    # 1. Clear ChromaDB collections (delete only this user's vectors)
    from anjo.memory.long_term import _get_collections

    try:
        semantic_col, emotional_col = _get_collections(user_id)
        for col in (semantic_col, emotional_col):
            try:
                ids = col.get(include=[])["ids"]
                if ids:
                    col.delete(ids=ids)
            except Exception as e:
                logger.warning(
                    f"Could not delete ChromaDB vectors for {user_id} in {col.name}: {e}"
                )
    except Exception as e:
        logger.warning(f"ChromaDB reset failed for {user_id}: {e}")

    # 2. Delete SelfCore current state + history snapshots
    from anjo.core.self_core import _core_dir

    core_dir = _core_dir(user_id)
    current_path = core_dir / "current.json"
    history_dir = core_dir / "history"
    if current_path.exists():
        current_path.unlink()
    if history_dir.exists():
        for f in history_dir.iterdir():
            if f.is_file():
                f.unlink()

    # 3. Clear permanent chat history
    from anjo.core.history import clear as clear_history

    clear_history(user_id)

    # 4. Clear reflection log + journal + persona (all injected into system prompt)
    from anjo.reflection.log import _log_path

    log_path = _log_path(user_id)
    if log_path.exists():
        log_path.unlink()
    from anjo.memory.journal import _user_dir as _journal_user_dir

    for fname in ("journal.md", "persona.md"):
        (_journal_user_dir(user_id) / fname).unlink(missing_ok=True)

    # 5. Reset the session with fresh SelfCore defaults
    from anjo.dashboard.session_store import delete_session, get_or_create_session

    delete_session(user_id)
    get_or_create_session(user_id)

    return {"ok": True}
