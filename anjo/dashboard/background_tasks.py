"""Background tasks and session tracking for the chat system.

Extracted from chat_routes.py to keep route handlers thin. Contains:
- Deduplication tracking (bounded ordered sets for reflection + quick-facts)
- Background thread spawners for quick-facts extraction and mid-session reflection
"""

from __future__ import annotations

import collections
import json as _json
import re
import threading

from anjo.core.logger import logger
from anjo.dashboard.session_store import (
    get_self_core_safe,
    refresh_cached_facts,
    set_session_core,
)

# ── Deduplication tracking ──────────────────────────────────────────────────

_SETS_LOCK = threading.Lock()
_MID_REFLECT_LOCK: set[str] = set()

_QUICK_FACTS_MAXSIZE = 2000
_REFLECTED_MAXSIZE = 2000
_QUICK_FACTS_DONE: collections.OrderedDict[tuple[str, str], None] = collections.OrderedDict()
_REFLECTED_SESSIONS: collections.OrderedDict[str, None] = collections.OrderedDict()


def _set_add(od: collections.OrderedDict, key, maxsize: int) -> bool:
    """Add key to ordered dict (used as bounded set). Returns True if newly added."""
    if key in od:
        return False
    od[key] = None
    if len(od) > maxsize:
        od.popitem(last=False)
    return True


def reflection_session_claim(sid: str) -> bool:
    """Atomically claim a full reflection for this session_id. False if already claimed."""
    with _SETS_LOCK:
        return _set_add(_REFLECTED_SESSIONS, sid, _REFLECTED_MAXSIZE)


def cleanup_session_tracking(user_id: str, session_id: str) -> None:
    """Remove session tracking entries. Called on session end (explicit or inactivity)."""
    with _SETS_LOCK:
        _QUICK_FACTS_DONE.pop((user_id, session_id), None)


# ── Background tasks ────────────────────────────────────────────────────────


def quick_facts_extract(user_id: str, session_id: str, transcript: list[dict]) -> None:
    """After 4 user messages: extract name + concrete facts in background using Haiku.

    Fires once per session. Updates the facts table and user_name on self_core immediately
    so subsequent messages in the same session already benefit.
    """
    key = (user_id, session_id)
    with _SETS_LOCK:
        if not _set_add(_QUICK_FACTS_DONE, key, _QUICK_FACTS_MAXSIZE):
            return

    def _run():
        try:
            from anjo.core.facts import merge_facts
            from anjo.core.llm import MODEL_BACKGROUND, get_client
            from anjo.core.self_core import SelfCore

            transcript_text = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in transcript)
            system = (
                "Extract concrete facts from this conversation.\n"
                'Return JSON only: {"user_name": str or null, "facts": ["fact1", ...]}\n'
                "- user_name: the user's first name if they stated it, else null\n"
                "- facts: up to 3 specific details the user explicitly shared "
                "(job, location, hobby, life circumstance). NOT impressions.\n"
                'If nothing concrete: {"user_name": null, "facts": []}'
            )
            response = get_client().messages.create(
                model=MODEL_BACKGROUND,
                max_tokens=150,
                system=system,
                messages=[{"role": "user", "content": transcript_text}],
            )
            if not response.content or not hasattr(response.content[0], "text"):
                logger.error(
                    f"Quick facts extraction: unexpected LLM response for {user_id}: {response}"
                )
                return
            raw = re.sub(r"^```(?:json)?\s*", "", response.content[0].text.strip())
            raw = re.sub(r"\s*```$", "", raw)
            data = _json.loads(raw)

            core_dict = get_self_core_safe(user_id)
            if not core_dict:
                return
            core = SelfCore.from_state(core_dict, user_id)
            changed = False
            if (name := data.get("user_name")) and not core.relationship.user_name:
                core.relationship.user_name = name
                changed = True
            facts = [str(f) for f in data.get("facts", []) if f]
            if facts:
                merge_facts(user_id, facts)
                refresh_cached_facts(user_id)
            if changed:
                core.save()
                set_session_core(user_id, core)
        except Exception as e:
            logger.error(f"Quick facts extraction failed for {user_id}: {e}")

    threading.Thread(target=_run, daemon=True).start()


def maybe_mid_reflect(user_id: str, transcript: list[dict]) -> None:
    """Spawn a background reflection without ending the session."""
    should_reflect = False
    with _SETS_LOCK:
        if user_id not in _MID_REFLECT_LOCK:
            _MID_REFLECT_LOCK.add(user_id)
            should_reflect = True
    if not should_reflect:
        return

    def _run():
        try:
            from anjo.core.self_core import SelfCore
            from anjo.reflection.engine import run_reflection

            core_dict = get_self_core_safe(user_id)
            if not core_dict:
                logger.warning(f"Mid-reflect: session gone for {user_id}, skipping")
                return
            live_core = SelfCore.from_state(core_dict, user_id)
            run_reflection(
                transcript=transcript,
                core=live_core,
                user_id=user_id,
                session_id=f"{user_id}_mid_{len(transcript)}",
                mid_session=True,
            )
            fresh_core = SelfCore.load(user_id)
            set_session_core(user_id, fresh_core)
            refresh_cached_facts(user_id)
        except Exception as e:
            logger.error(f"Mid-session reflection error: {e}")
        finally:
            with _SETS_LOCK:
                _MID_REFLECT_LOCK.discard(user_id)

    threading.Thread(target=_run, daemon=True).start()
