"""Background watcher tasks for the Anjo dashboard.

Extracted from app.py to break the God Module pattern.
Watchers run as asyncio tasks inside the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio

from anjo.core.logger import logger

INACTIVITY_CHECK_INTERVAL = 60  # seconds between checks
DRIFT_CHECK_INTERVAL = 3600  # seconds — checks hourly, applies at most daily

_REFLECTING_LOCK: set[str] = set()


def _log_reflection_exception(task: asyncio.Task) -> None:
    """Done-callback for reflection tasks — ensures errors are never silent."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("Reflection task failed: %s", exc, exc_info=exc)


async def _inactivity_watcher() -> None:
    """Background task: reflect on sessions that have gone quiet."""
    from anjo.core.self_core import SelfCore
    from anjo.core.transcript_queue import delete_pending, save_pending
    from anjo.dashboard.background_tasks import cleanup_session_tracking, reflection_session_claim
    from anjo.dashboard.session_store import check_and_cleanup_session, get_inactive_sessions
    from anjo.reflection.engine import run_reflection

    _reflection_semaphore = asyncio.Semaphore(5)  # limit concurrent reflections
    while True:
        await asyncio.sleep(INACTIVITY_CHECK_INTERVAL)
        try:
            for session in get_inactive_sessions():
                user_id = session["user_id"]
                if user_id in _REFLECTING_LOCK:
                    continue

                full_history = session["state"].get("conversation_history", [])
                seed_len = session["state"].get("seed_len", 0)
                transcript = full_history[seed_len:] if seed_len > 0 else full_history

                sid = session["session_id"]
                pending_path = save_pending(transcript, user_id, sid)

                live_core = SelfCore.from_state(session["state"]["self_core"], user_id)
                last_activity = session.get("last_activity")

                # Mark BEFORE creating task to close race window
                _REFLECTING_LOCK.add(user_id)

                async def _run_reflection_task(
                    t=transcript,
                    c=live_core,
                    u=user_id,
                    s=sid,
                    p=pending_path,
                    la=last_activity,
                ):
                    try:
                        async with _reflection_semaphore:
                            reflected_ok = False

                            def _reflect():
                                nonlocal reflected_ok
                                if not reflection_session_claim(s):
                                    logger.info(
                                        "Inactivity reflection skipped (already reflected): %s", s
                                    )
                                    return
                                try:
                                    run_reflection(
                                        transcript=t,
                                        core=c,
                                        user_id=u,
                                        session_id=s,
                                        last_activity=la,
                                    )
                                    delete_pending(p)
                                    reflected_ok = True
                                except Exception as e:
                                    logger.error(f"Inactivity reflection failed for {s}: {e}")

                            await asyncio.to_thread(_reflect)

                            if reflected_ok:
                                was_stale = check_and_cleanup_session(u, la or 0)
                                if was_stale:
                                    cleanup_session_tracking(u, s)
                                logger.info(f"Auto-reflected session {s} after inactivity")
                    finally:
                        _REFLECTING_LOCK.discard(u)

                task = asyncio.create_task(_run_reflection_task())
                task.add_done_callback(_log_reflection_exception)

        except Exception as e:
            logger.error(f"Inactivity watcher error: {e}")


async def _drift_watcher() -> None:
    """Background task: apply daily state drift and AutoDream consolidation for all users."""
    from anjo.core.drift import run_autodream_for_all_users, run_drift_for_all_users

    while True:
        await asyncio.sleep(DRIFT_CHECK_INTERVAL)
        try:
            await asyncio.to_thread(run_drift_for_all_users)
            await asyncio.to_thread(run_autodream_for_all_users)
        except Exception as e:
            logger.error(f"Drift watcher error: {e}")
