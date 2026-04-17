"""Chat API routes with SSE streaming.

Production uses the pre_response_graph (LangGraph) for orchestration:
  perceive → gate → [retrieve →] appraise → policy → END
Then streams the LLM response separately via Anthropic SDK.
Billing is a pre/post wrapper around the graph + streaming.

Background tasks (quick-facts extraction, mid-session reflection) and
deduplication tracking live in ``anjo.dashboard.background_tasks``.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import threading

# Extended thinking — off by default; incompatible with prompt caching
_THINKING_ENABLED = os.environ.get("ANJO_THINKING_ENABLED", "false").lower() == "true"
_THINKING_BUDGET  = int(os.environ.get("ANJO_THINKING_BUDGET", "3000"))

from fastapi import APIRouter, Body, Depends
from fastapi.responses import StreamingResponse

from anjo.dashboard.auth import get_current_user_id
from anjo.core.logger import logger
from anjo.dashboard.session_store import (
    accumulate_tokens,
    delete_session,
    get_or_create_session,
    get_self_core_safe,
    get_session,
    get_session_snapshot,
    touch_session,
    update_session_state,
)
from anjo.dashboard.background_tasks import (
    cleanup_session_tracking,
    quick_facts_extract,
    maybe_mid_reflect,
    reflection_session_claim,
)

router = APIRouter()


@router.get("/chat/history")
def get_history(user_id: str = Depends(get_current_user_id)):
    """Return full persistent chat history from SQLite."""
    from anjo.core.history import get_history as _get
    return {"history": _get(user_id)}


@router.post("/chat/start")
def start_session(tz_offset: int = 0, user_id: str = Depends(get_current_user_id)):
    from anjo.dashboard.session_store import _sessions_lock
    session_id = get_or_create_session(user_id)
    pending_outreach = None
    with _sessions_lock:
        session = get_session(user_id)
        if session:
            session["state"]["tz_offset"] = tz_offset
            pending_outreach = session.pop("pending_outreach", None)
    return {"session_id": session_id, "pending_outreach": pending_outreach}


@router.post("/chat/{session_id}/message")
async def chat_message(session_id: str, text: str = Body(..., embed=True), user_id: str = Depends(get_current_user_id)):
    from fastapi.responses import JSONResponse
    import unicodedata
    # Strip Unicode zero-width / invisible characters before any other check
    _ZERO_WIDTH = {'\u200b', '\u200c', '\u200d', '\ufeff', '\u2060', '\u180e'}
    text = ''.join(c for c in unicodedata.normalize('NFC', text) if c not in _ZERO_WIDTH)
    if not text.strip():
        return JSONResponse({"error": "Message cannot be empty"}, status_code=400)
    if len(text) > 4000:
        return JSONResponse({"error": "Message too long (max 4000 characters)"}, status_code=400)
    # Auto-create session if missing (e.g. server restarted while browser tab was open)
    if not get_session(user_id):
        get_or_create_session(user_id)
    session = get_session(user_id)
    assert session is not None  # guaranteed by get_or_create_session above

    state = copy.deepcopy(session["state"])
    state["user_message"] = text.strip()
    state["retrieved_memories"] = []
    state["assistant_response"] = ""
    state["active_emotions"] = {}
    state["intent"] = ""
    # occ_carry intentionally NOT reset — appraise_node decays it across turns
    state["stance"] = ""
    state["stance_directive"] = ""
    state["session_id"] = session_id

    async def event_stream():
        nonlocal state

        # Credit gate first — before any LLM work or history writes
        from anjo.core.subscription import can_send_message, get_tier, deduct_message_count
        if not can_send_message(user_id):
            tier = get_tier(user_id)
            yield f"event: no_credits\ndata: {json.dumps({'tier': tier})}\n\n"
            return

        # Persist user message to SQLite before the graph runs
        from anjo.core.history import append_message as _append
        _append(user_id, "user", text.strip())

        # Run orchestration graph: perceive → gate → [retrieve →] appraise → policy
        from anjo.graph.conversation_graph import pre_response_graph
        try:
            state = await pre_response_graph.ainvoke(state)
        except Exception as e:
            logger.error(f"Pre-response graph failed for {user_id}: {e}")
            yield f"event: error\ndata: {json.dumps({'error': 'Service temporarily unavailable'})}\n\n"
            return

        # Silence path — gate_node routed to END without appraise/policy
        if not state.get("should_respond", True):
            update_session_state(user_id, state)
            touch_session(user_id)
            yield f"event: done\ndata: {json.dumps({'full_text': '', 'retrieved_memories': [], 'did_retrieve': False, 'active_emotions': state.get('active_emotions', {}), 'intent': state.get('intent', ''), 'silent': True})}\n\n"
            return

        from anjo.core.self_core import SelfCore
        from anjo.core.prompt_builder import build_system_prompt

        core = SelfCore.from_state(state["self_core"], user_id)
        user_turn_count = sum(1 for m in state["conversation_history"] if m["role"] == "user")
        static_block, dynamic_block = build_system_prompt(
            core,
            state.get("retrieved_memories") or [],
            state.get("active_emotions"),
            tz_offset=state.get("tz_offset", 0),
            user_turn_count=user_turn_count,
            seed_len=state.get("seed_len", 0),
            user_facts=state.get("cached_user_facts"),
            trending_topics=state.get("cached_trending_topics"),
            stance_directive=state.get("stance_directive", ""),
        )

        from anjo.core.llm import get_client, MODEL, MODEL_BACKGROUND
        from anjo.core.subscription import get_model_for_user
        loop = asyncio.get_event_loop()

        _model_key = get_model_for_user(user_id)
        _model_id  = MODEL if _model_key == "sonnet" else MODEL_BACKGROUND

        full_text = ""
        token_queue: asyncio.Queue = asyncio.Queue()

        def _producer():
            nonlocal full_text
            try:
                llm_history = state["conversation_history"][-40:]
                if _THINKING_ENABLED:
                    # Extended thinking is mutually exclusive with prompt caching
                    stream_kwargs: dict = dict(
                        model=_model_id,
                        max_tokens=4096,
                        thinking={"type": "enabled", "budget_tokens": _THINKING_BUDGET},
                        system=[
                            {"type": "text", "text": static_block},
                            {"type": "text", "text": dynamic_block},
                        ],
                        messages=llm_history,
                    )
                else:
                    stream_kwargs = dict(
                        model=_model_id,
                        max_tokens=4096,
                        system=[
                            {"type": "text", "text": static_block, "cache_control": {"type": "ephemeral"}},
                            {"type": "text", "text": dynamic_block},
                        ],
                        messages=llm_history,
                        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
                    )
                with get_client().messages.stream(**stream_kwargs) as stream:
                    for chunk in stream.text_stream:
                        full_text += chunk
                        loop.call_soon_threadsafe(token_queue.put_nowait, ("token", chunk))
                    usage = stream.get_final_message().usage
                    loop.call_soon_threadsafe(
                        token_queue.put_nowait,
                        ("done", {"input": usage.input_tokens, "output": usage.output_tokens}),
                    )
            except Exception as e:
                logger.error(f"LLM stream error for {user_id}: {e}")
                loop.call_soon_threadsafe(token_queue.put_nowait, ("error", "Service temporarily unavailable"))

        t = threading.Thread(target=_producer, daemon=True)
        t.start()

        while True:
            try:
                kind, data = await asyncio.wait_for(token_queue.get(), timeout=30)
            except asyncio.TimeoutError:
                yield f"event: error\ndata: {json.dumps({'error': 'stream timeout'})}\n\n"
                break

            if kind == "token":
                yield f"event: token\ndata: {json.dumps({'text': data})}\n\n"
            elif kind == "done":
                updated_history = state["conversation_history"] + [
                    {"role": "assistant", "content": full_text}
                ]
                state["conversation_history"] = updated_history
                state["assistant_response"] = full_text
                _append(user_id, "assistant", full_text)

                # Fetch live self_core under lock to capture any concurrent updates (e.g. from mid-reflect)
                _fresh_core = get_self_core_safe(user_id)
                if _fresh_core:
                    state["self_core"] = _fresh_core

                update_session_state(user_id, state)
                touch_session(user_id)
                if isinstance(data, dict):
                    in_tok = data.get("input", 0)
                    out_tok = data.get("output", 0)
                    accumulate_tokens(user_id, in_tok, out_tok)
                    deduct_message_count(user_id)

                user_msg_count = sum(1 for m in updated_history if m["role"] == "user")
                if user_msg_count == 4:
                    quick_facts_extract(user_id, session_id, updated_history)
                if len(updated_history) % 20 == 0:
                    maybe_mid_reflect(user_id, updated_history)

                from anjo.core.self_core import SelfCore as _SC
                _c = _SC.model_validate(state["self_core"])
                _mood = {"valence": _c.mood.valence, "arousal": _c.mood.arousal, "dominance": _c.mood.dominance}
                _att  = {"longing": _c.attachment.longing, "weight": _c.attachment.weight}
                _raw_mems = state.get('retrieved_memories', [])
                _mem_strings = [doc for _, doc in _raw_mems] if _raw_mems and isinstance(_raw_mems[0], tuple) else _raw_mems
                yield f"event: done\ndata: {json.dumps({'full_text': full_text, 'retrieved_memories': _mem_strings, 'did_retrieve': state.get('should_retrieve', False), 'active_emotions': state.get('active_emotions', {}), 'intent': state.get('intent', ''), 'mood': _mood, 'attachment': _att})}\n\n"
                break
            elif kind == "error":
                yield f"event: error\ndata: {json.dumps({'error': data})}\n\n"
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/{session_id}/end")
def end_session(session_id: str, user_id: str = Depends(get_current_user_id)):
    cleanup_session_tracking(user_id, session_id)
    if not get_session(user_id):
        get_or_create_session(user_id)
    session = get_session_snapshot(user_id)
    assert session is not None  # guaranteed by get_or_create_session above

    # Extract data BEFORE deleting session
    full_history = session["state"].get("conversation_history", [])
    seed_len = session["state"].get("seed_len", 0)
    transcript = full_history[seed_len:] if seed_len > 0 else full_history
    sid = session["session_id"]
    last_activity = session.get("last_activity")

    from anjo.core.self_core import SelfCore
    core = SelfCore.from_state(session["state"]["self_core"], user_id)

    do_reflect = reflection_session_claim(sid) if transcript else False
    if transcript and not do_reflect:
        logger.warning(f"Session {sid} already reflected, skipping duplicate")

    delete_session(user_id)

    if do_reflect and transcript:
        from anjo.core.transcript_queue import save_pending, delete_pending
        from anjo.reflection.engine import run_reflection
        pending_path = save_pending(transcript, user_id, sid)
        try:
            run_reflection(transcript=transcript, core=core, user_id=user_id, session_id=sid, last_activity=last_activity)
            delete_pending(pending_path)
        except Exception as e:
            logger.error(f"Reflection error for {user_id}: {e}")
            return {"ok": False, "error": "Reflection failed", "reflected": False}

    get_or_create_session(user_id)
    return {"ok": True, "reflected": bool(transcript)}
