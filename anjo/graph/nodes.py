"""LangGraph node functions for the Anjo conversation graph.

Nodes receive an AnjoState Pydantic model instance and return partial dicts.
LangGraph merges the returned dict into the accumulated state.
"""
from __future__ import annotations

from anjo.core.llm import get_client, MODEL
from anjo.core.self_core import SelfCore
from anjo.core.prompt_builder import build_system_prompt
from anjo.graph.state import AnjoState
from anjo.memory.retrieval_classifier import should_retrieve
from anjo.core.logger import logger


def _coerce_llm_bool(value, default: bool) -> bool:
    """Interpret JSON boolean fields from LLM output; non-empty strings are not blindly truthy."""
    if value is True or value is False:
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "1", "yes"):
            return True
        if s in ("false", "0", "no"):
            return False
    return default


def perceive_node(state: AnjoState) -> dict:
    """Append the user's message to conversation history."""
    history = list(state.conversation_history) + [{"role": "user", "content": state.user_message}]
    return {"conversation_history": history}


def classify_node(state: AnjoState) -> dict:
    """Decide whether long-term memory retrieval is warranted.

    Always retrieves on the first message of a session so Anjo knows where things
    were left regardless of what the user says to open.
    """
    is_first = len(state.conversation_history) == state.seed_len + 1
    return {"should_retrieve": is_first or should_retrieve(state.user_message)}


_OCC_CARRY_DECAY = {
    "reproach":   0.70,  # blame fades slowly — still ~34% after 3 turns
    "distress":   0.80,  # hurt fades moderately
    "admiration": 0.85,  # positive agent-appraisal fades faster than negative
    "gratitude":  0.88,  # gratitude lingers — being trusted/thanked stays present
    "joy":        0.90,  # positive event-emotion dissipates quickest
}


def appraise_node(state: AnjoState) -> dict:
    """OCC appraisal + PAD mood update. Classifies intent and evaluates against Anjo's goals.

    Implements intra-session emotion carry: OCC emotions decay across turns rather than
    resetting to zero. Reproach from ABUSE persists for several messages so an immediate
    apology doesn't fully clear it.
    """
    from anjo.core.emotion import classify_intent_llm

    core = SelfCore.from_state(state.self_core, state.user_id or "default")
    core.decay_mood()
    core.blend_baseline()
    # Use intent already classified by gate_node if available (avoids a duplicate Haiku call)
    intent = state.intent or classify_intent_llm(
        state.user_message, user_id=state.user_id, session_id=state.session_id
    )
    fresh_emotions = core.appraise_input(intent)

    # Decay carried emotions from previous turns, then merge with fresh appraisal.
    carried = state.occ_carry or {}
    decayed_carry = {
        k: v * _OCC_CARRY_DECAY.get(k, 0.80)
        for k, v in carried.items()
        if v * _OCC_CARRY_DECAY.get(k, 0.80) > 0.05
    }
    merged = {
        k: max(fresh_emotions.get(k, 0.0), decayed_carry.get(k, 0.0))
        for k in set(fresh_emotions) | set(decayed_carry)
    }

    # ── State-derived emotions — not triggered by user intent, but by SelfCore ─
    state_emotions: dict[str, float] = {}

    if core.mood.arousal < 0:
        state_emotions["fatigue"] = round(min(1.0, -core.mood.arousal), 3)
    if core.attachment.longing > 0.3:
        state_emotions["longing"] = round(core.attachment.longing, 3)
    if -0.3 < core.mood.valence < 0:
        state_emotions["unease"] = round(min(0.3, -core.mood.valence), 3)

    for k, v in state_emotions.items():
        if v > merged.get(k, 0.0):
            merged[k] = v

    occ_carry_new = {k: v for k, v in merged.items() if v > 0.05}

    return {
        "active_emotions": merged,
        "intent": intent,
        "self_core": core.model_dump(),
        "occ_carry": occ_carry_new,
    }


def policy_node(state: AnjoState) -> dict:
    """Select conversational stance based on emotional state, intent, and relationship.

    Deterministic — no LLM call. Returns stance + directive text for prompt injection.
    """
    from anjo.core.policy import decide_stance

    core = SelfCore.from_state(state.self_core, state.user_id or "default")

    has_threads = False
    try:
        from anjo.memory.memory_graph import get_open_threads
        threads = get_open_threads(state.user_id)
        has_threads = len(threads) > 0
    except Exception:
        pass

    stance, directive = decide_stance(
        core=core,
        intent=state.intent or "CASUAL",
        active_emotions=state.active_emotions,
        has_open_threads=has_threads,
    )

    return {
        "stance": stance.value,
        "stance_directive": directive,
    }


def retrieve_node(state: AnjoState) -> dict:
    """Query long-term memory and return relevant memories as (score, text) tuples.

    Always prepends the most recent session summary at score=1.0 (always high certainty)
    so Anjo knows where things were left, independent of semantic relevance.
    Semantic retrieval fills the rest as (score, text) tuples for skeptical framing.
    """
    from anjo.memory.long_term import query_memories, get_last_session_summary

    memories: list[tuple[float, str]] = []

    last = get_last_session_summary(state.user_id)
    if last:
        memories.append((1.0, f"[Last session] {last}"))

    scored = query_memories(state.user_message, user_id=state.user_id, n_results=4)
    seen_docs = {last} if last else set()
    for score, doc in scored:
        if doc not in seen_docs:
            memories.append((score, doc))
            seen_docs.add(doc)

    return {"retrieved_memories": memories[:5]}


_SILENCE_SYSTEM = """\
You are reading Anjo's internal state and deciding whether she would respond to a message.

Anjo sometimes chooses silence. Not as rejection, not as malfunction — as a real response.

She goes quiet when:
- The conversation reached a natural, complete ending and the user's message is a clear goodbye
  or a warm close. Responding would cheapen it.
- The user was hostile or dismissive and her sense of self (autonomy, dominance) is high enough
  that she's not going to engage. She doesn't reward poor treatment with attention.
- The message is so minimal or low-effort that engaging would feel like performance.

She always responds when:
- The user is asking something genuine, sharing something, or continuing a thread.
- There's any ambiguity — silence is only for clear cases.
- The relationship is new (stranger/acquaintance). She doesn't have the standing to withhold yet.

Return valid JSON only:
{"respond": true}
or
{"respond": false, "reason": "one short phrase"}
"""


_GATE_SYSTEM = """\
You are Anjo's internal router. Given a user message and conversation context, return a JSON object.

Return exactly:
{"intent": "...", "retrieve": true/false, "respond": true/false}

intent — one of: ABUSE, APOLOGY, VULNERABILITY, CURIOSITY, CHALLENGE, NEGLECT, CASUAL

retrieve — true if:
- User references a past event, specific detail from a previous conversation, or asks what you remember
- Topic is emotional and likely benefits from recalled context (grief, loss, long-running struggles)
- First message of a session (always true regardless of content)
- False for small talk, general questions, or anything self-contained in the current exchange

respond — false only if ALL of these are true:
- Relationship stage is friend, close, or intimate (not stranger/acquaintance)
- The message is a clear goodbye/warm close AND continuing would cheapen it, OR
  the user was hostile/dismissive AND Anjo's autonomy is high enough not to reward it, OR
  the message is so minimal it warrants no response
- When in doubt: true. Silence is a deliberate choice, not a default.

Return valid JSON only. No explanation."""


def gate_node(state: AnjoState) -> dict:
    """Single Haiku gate: determines intent, whether to retrieve memory, and whether to respond.

    Replaces the separate classify_node + silence_node calls. One LLM call
    on the critical path instead of two. Falls back to rule-based on any error.
    """
    import json as _json
    from anjo.core.emotion import _is_abuse, classify_intent
    from anjo.memory.retrieval_classifier import should_retrieve as _should_retrieve_rules
    from anjo.core.llm import get_client, MODEL_BACKGROUND

    msg = state.user_message
    lower = msg.lower().strip()

    if _is_abuse(msg, lower):
        return {"intent": "ABUSE", "should_retrieve": False, "should_respond": True}

    is_first = len(state.conversation_history) == state.seed_len + 1

    core = SelfCore.from_state(state.self_core, state.user_id or "default")

    history = state.conversation_history
    recent = history[-4:] if len(history) > 4 else history
    context_lines = "\n".join(
        f"{m['role'].upper()}: {m['content'][:120]}" for m in recent
    )

    user_prompt = f"""\
Relationship stage: {core.relationship.stage}
Anjo autonomy: {core.goals.autonomy:.2f}, dominance: {core.mood.dominance:.2f}
First turn this session: {is_first}

Recent exchange:
{context_lines}

Current message: {msg}"""

    try:
        resp = get_client().messages.create(
            model=MODEL_BACKGROUND,
            max_tokens=40,
            system=[{"type": "text", "text": _GATE_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_prompt}],
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
        )
        if not resp.content or not hasattr(resp.content[0], 'text'):
            raise ValueError("empty gate response")
        raw = resp.content[0].text.strip().strip("```json").strip("```").strip()
        data = _json.loads(raw)
        intent = str(data.get("intent", "CASUAL")).upper()
        from anjo.core.emotion import _VALID_INTENTS
        if intent not in _VALID_INTENTS:
            intent = classify_intent(msg)
        retrieve = _coerce_llm_bool(data.get("retrieve"), False) or is_first
        respond = _coerce_llm_bool(data.get("respond"), True)
        return {"intent": intent, "should_retrieve": retrieve, "should_respond": respond}
    except Exception as e:
        logger.warning(f"gate_node LLM failed, falling back to rule-based: {e}")
        return {
            "intent": classify_intent(msg),
            "should_retrieve": is_first or _should_retrieve_rules(msg),
            "should_respond": True,
        }


def silence_node(state: AnjoState) -> dict:
    """Decide whether Anjo responds at all. Returns should_respond: bool.

    Uses a fast Haiku call. Defaults to True on any error — silence is a choice,
    not a fallback for failures.
    """
    from anjo.core.llm import get_client, MODEL_BACKGROUND
    import json

    core = SelfCore.from_state(state.self_core, state.user_id or "default")

    if core.relationship.stage_int < 3:
        return {"should_respond": True}

    history = state.conversation_history
    user_msg = state.user_message

    recent = history[-6:] if len(history) > 6 else history
    transcript_lines = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in recent
    )

    reproach = state.active_emotions.get("reproach", 0.0)
    autonomy = core.goals.autonomy
    dominance = core.mood.dominance

    user_prompt = f"""\
Relationship stage: {core.relationship.stage}
Anjo's autonomy goal: {autonomy:.2f}
Anjo's current dominance: {dominance:.2f}
Reproach level from this message: {reproach:.2f}

Recent conversation:
{transcript_lines}

Current message from user: {user_msg}

Would Anjo respond to this, or go quiet?"""

    try:
        response = get_client().messages.create(
            model=MODEL_BACKGROUND,
            max_tokens=60,
            system=[{"type": "text", "text": _SILENCE_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_prompt}],
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
        )
        if not response.content or not hasattr(response.content[0], 'text'):
            logger.warning("silence_node: empty or malformed response — defaulting to respond")
            return {"should_respond": True}
        raw = response.content[0].text.strip().strip("```json").strip("```").strip()
        data = json.loads(raw)
        should = _coerce_llm_bool(data.get("respond"), True)
        if not should:
            logger.info(f"Anjo chose not to respond: {data.get('reason', '')}")
        return {"should_respond": should}
    except Exception as e:
        logger.error(f"silence_node error: {e} — defaulting to respond")
        return {"should_respond": True}


def respond_node(state: AnjoState) -> dict:
    """Call Claude and produce Anjo's response (blocking, non-streaming)."""
    core = SelfCore.from_state(state.self_core, state.user_id or "default")
    static_block, dynamic_block = build_system_prompt(
        core,
        state.retrieved_memories or [],
        state.active_emotions,
    )
    cached_system = [
        {"type": "text", "text": static_block, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dynamic_block},
    ]

    llm_history = state.conversation_history[-40:]
    response = get_client().messages.create(
        model=MODEL,
        max_tokens=1024,
        system=cached_system,
        messages=llm_history,
        extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
    )

    if not response.content or not hasattr(response.content[0], 'text'):
        raise ValueError("respond_node: LLM returned empty or malformed content")
    assistant_text = response.content[0].text
    updated_history = list(state.conversation_history) + [
        {"role": "assistant", "content": assistant_text}
    ]

    return {
        "assistant_response": assistant_text,
        "conversation_history": updated_history,
    }
