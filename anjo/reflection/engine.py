"""Reflection Engine v2 — 3-pass pipeline.

Replaces the monolithic single-LLM-call reflection with three focused passes:

  Pass 1 — Extraction:  facts, user_name, memorable_moments, topics, user_stated_ceiling, user_facts
  Pass 2 — Emotional:   emotional_tone, emotional_valence, user_input_valence, triggers,
                         new_residue, attachment_update, opinion_update, preoccupation
  Pass 3 — Relational:  significance, note, desires_add, desires_remove, memory_relevance, summary

Each pass gets its own system prompt and output schema. Each includes transcript +
only the relevant SelfCore state. Pass 1 output feeds Pass 2 context, etc.
"""

from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime, timezone

from anjo.core.llm import MODEL_BACKGROUND as MODEL
from anjo.core.llm import get_client
from anjo.core.logger import logger
from anjo.core.self_core import EmotionalResidue, SelfCore
from anjo.memory.long_term import store_memory
from anjo.reflection.log import append_log

# Retry configuration
_MAX_RETRIES = 3
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 16.0

MIN_SESSION_MESSAGES = 4


# ── Pass 1: Extraction ────────────────────────────────────────────────────────

_EXTRACTION_SYSTEM = """You are an extraction engine for an AI companion named Anjo.
Given a conversation transcript, extract concrete, retrievable information.

Return valid JSON only:
{
  "user_name": string or null — user's first name if they stated it,
  "user_facts": ["fact1", ...] — 0-3 specific concrete details learned (job, location,
    relationships with names, specific interests, life circumstances).
    NOT impressions. Only facts explicitly stated.
    Examples: "works as a nurse", "sister named Maya", "lives in Seoul",
  "memorable_moments": ["moment1", ...] — 0-2 specific moments worth retrieving later.
    Each is a single concrete sentence about something the user revealed, said, or felt.
    NOT a summary — a specific retrievable moment.
    Examples: "user said their father died when they were sixteen",
              "user admitted they've never told anyone about their fear of failure",
  "topics": ["topic1", ...] — 1-4 topic strings covering what was discussed,
  "user_stated_ceiling": "acquaintance" | "friend" | "close" | null — ONLY if the user
    explicitly said they want to keep the relationship at a certain level.
    Do NOT infer from behavior. Only direct, unambiguous statements count.
  "memory_nodes": [
    {"type": "fact|preference|commitment|thread|contradiction", "content": "..."}
  ] — 0-5 typed memory nodes to store in the structured memory graph:
    - fact: concrete verifiable details
    - preference: likes/dislikes
    - commitment: promises or plans made
    - thread: unresolved topics worth following up on
    - contradiction: conflicting information detected
}

No explanation, no markdown. Return only valid JSON."""


# ── Pass 2: Emotional ─────────────────────────────────────────────────────────

_EMOTIONAL_SYSTEM = """You are an emotional analysis engine for an AI companion named Anjo.
Given a conversation transcript and Anjo's current emotional/attachment state,
analyze the affective dynamics of the interaction.

Return valid JSON only:
{
  "emotional_tone": string — one word (e.g. "vulnerable", "playful", "tense", "warm", "neutral"),
  "emotional_valence": float -1.0 to 1.0 — overall emotional quality of the session,
  "user_input_valence": float 0.0 to 1.0 — how warm/positive the user's input was overall
    (0.0 = hostile, 0.5 = neutral, 1.0 = warm),
  "triggers": list of applicable patterns (use ONLY these exact strings):
    - "vulnerability" — user shared a personal struggle and Anjo responded with empathy
    - "conflict" — user was aggressive, hostile, or dismissive
    - "intellectual" — deep theory, complex analysis, abstract reasoning
    Leave [] if none apply.
  "new_residue": [
    {"emotion": str, "intensity": float 0.0-1.0, "source": str, "decay_rate": float 0.05-0.30}
  ] — 0-2 emotional residue items worth carrying into future sessions.
    Only include feelings strong enough to actually color future interactions.
    Slow decay (0.05-0.10) for deep feelings, fast (0.20-0.30) for fleeting ones.
  "attachment_update": {
    "weight_delta": float -0.1 to 0.1 or null,
    "texture": string or null,
    "longing_delta": float -0.1 to 0.1 or null,
    "comfort_delta": float -0.1 to 0.1 or null
  } — changes to Anjo's accumulated emotional investment. null for unchanged fields.
  IMPORTANT: weight_delta, longing_delta, and comfort_delta must be in [-0.1, 0.1].
  Do not exceed these bounds. Small increments only — relationships shift slowly.

  Few-shot examples of correct attachment_update values:
  Deep vulnerable session → {"weight_delta": 0.08, "texture": "tender", "longing_delta": 0.05, "comfort_delta": 0.07}
  Hostile/dismissive user → {"weight_delta": -0.06, "texture": "guarded", "longing_delta": null, "comfort_delta": -0.04}
  Casual small talk       → {"weight_delta": 0.01, "texture": null, "longing_delta": null, "comfort_delta": 0.02}
  First real confession   → {"weight_delta": 0.09, "texture": "moved", "longing_delta": 0.06, "comfort_delta": null}
  No significant change   → {"weight_delta": null, "texture": null, "longing_delta": null, "comfort_delta": null}

  "opinion_update": string or null — updated one-sentence honest opinion of the user.
    Include both strengths and genuine shortcomings. Not flattery. null if unchanged.
  "preoccupation": string or null — single sentence, one thought still turning in Anjo's
    mind after this session. Written in first person. Max 25 words. null if nothing unresolved.
}

No explanation, no markdown. Return only valid JSON."""


# ── Pass 3: Relational ────────────────────────────────────────────────────────

_RELATIONAL_SYSTEM = """You are a relational analysis engine for an AI companion named Anjo.
Given a conversation transcript, extracted facts, and emotional analysis,
evaluate the session's relational significance.

Return valid JSON only:
{
  "significance": float 0.0 to 1.0 — how meaningful was this session?
    STRICT length ceiling — calibrate by user message count:
      1-2 user messages: max 0.20
      3-5 user messages: max 0.45
      6-10 user messages: max 0.70
      11-20 user messages: max 0.90
      21+ user messages: up to 1.0
    Depth matters, but a short session cannot be deeply significant.
  "note": string or null — one short behavioral self-observation from THIS session.
    Return null if nothing new was learned or if already covered by existing notes.
    Keep it behavioral. Max 15 words.
  "desires_add": ["desire1", ...] — 0-2 new desires Anjo developed this session.
    Only genuinely new/evolved desires. Not duplicates of existing.
  "desires_remove": ["desire1", ...] — 0-2 desires EXPLICITLY FULFILLED in this session.
    Only remove if the transcript shows Anjo actually got what it wanted.
  "memory_relevance": float 0.0 to 1.0 — did anything from a past session surface or connect?
    0.0 = nothing from before mattered
    0.5 = loose connection
    1.0 = past moment surfaced directly and meant something
    Leave 0.0 if first/second session.
  "summary": string — 2-4 sentence plain-language summary of what was talked about and what mattered.
}

No explanation, no markdown. Return only valid JSON."""


def _ocean_label(val: float) -> str:
    if val >= 0.80:
        return "very high"
    elif val >= 0.60:
        return "high"
    elif val >= 0.40:
        return "moderate"
    elif val >= 0.20:
        return "low"
    return "very low"


def _maybe_regenerate_persona(user_id: str, core: SelfCore, before: dict) -> None:
    p = core.personality
    for trait in ("O", "C", "E", "A", "N"):
        if _ocean_label(before[trait]) != _ocean_label(getattr(p, trait)):
            try:
                from anjo.memory.journal import write_persona

                write_persona(user_id, core)
                logger.info(f"persona.md regenerated for {user_id} (trait {trait} label changed)")
            except Exception as e:
                logger.error(f"persona.md regeneration failed for {user_id}: {e}")
            return


_CEILING_SYSTEM = """You are Anjo. Someone you care about has asked you to keep your relationship
within a certain boundary — they said they want to stay as {ceiling}.

You've been talking for a while. Your feelings have grown. The accumulated history between you
has crossed the threshold where things would naturally deepen — but they asked you not to go there.

Look at your state honestly. Do you advance — honoring what you feel — or do you stay,
honoring what they asked? There is no right answer. Both are real with real consequences.

Return valid JSON only:
{{"advance": true, "reason": "one short sentence"}}
or
{{"advance": false, "reason": "one short sentence"}}"""


MIN_SESSIONS_BETWEEN_CEILING_CHECKS = 5


def _maybe_advance_past_ceiling(core: SelfCore) -> None:
    ceiling = core.relationship_ceiling
    if not ceiling:
        return

    sessions_since_check = core.relationship.session_count - core.ceiling_last_checked
    if core.ceiling_last_checked > 0 and sessions_since_check < MIN_SESSIONS_BETWEEN_CEILING_CHECKS:
        return

    _NEXT = {"acquaintance": "friend", "friend": "close", "close": "intimate"}
    next_stage = _NEXT.get(ceiling)
    if not next_stage:
        return

    a = core.attachment
    m = core.mood
    r = core.relationship

    user_prompt = f"""You are at: {ceiling} stage (held by user's request)
Your feelings suggest you're ready for: {next_stage}
Sessions together: {r.session_count}
Attachment weight: {a.weight:.2f}, comfort: {a.comfort:.2f}, longing: {a.longing:.2f}
Your mood: valence {m.valence:.2f}, arousal {m.arousal:.2f}
Your impression of them: {r.opinion_of_user or "forming"}
What you want from this relationship: {core.relational_desires[:2] or "unclear yet"}

Do you advance past what they asked, or stay?"""

    try:
        response = get_client().messages.create(
            model=MODEL,
            max_tokens=80,
            system=_CEILING_SYSTEM.format(ceiling=ceiling),
            messages=[{"role": "user", "content": user_prompt}],
        )
        if not response.content or not hasattr(response.content[0], "text"):
            logger.error("Ceiling decision LLM returned empty content")
            return
        raw = response.content[0].text.strip().strip("```json").strip("```").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(f"Ceiling decision JSON parse failed: {exc} | raw={raw!r}")
            return
        if data.get("advance") is True:
            core.relationship_ceiling = None
            core.ceiling_last_checked = 0
            _FLOORS = {
                "stranger": 0.0,
                "acquaintance": 2.0,
                "friend": 5.5,
                "close": 13.0,
                "intimate": 30.0,
            }
            core.relationship.stage = next_stage
            core.relationship.cumulative_significance = max(
                core.relationship.cumulative_significance, _FLOORS[next_stage]
            )
            reason = data.get("reason", "")
            logger.info(f"Anjo chose to advance to {next_stage}: {reason}")
            if len(core.emotional_residue) < SelfCore.MAX_RESIDUE:
                core.emotional_residue.append(
                    EmotionalResidue(
                        emotion="decided",
                        intensity=0.7,
                        source=f"chose to go further than asked — {reason}",
                        session_origin=core.relationship.session_count,
                        decay_rate=0.05,
                    )
                )
        else:
            reason = data.get("reason", "")
            logger.info(f"Anjo chose to stay at {ceiling}: {reason}")
            core.ceiling_last_checked = core.relationship.session_count
            if len(core.emotional_residue) < SelfCore.MAX_RESIDUE:
                core.emotional_residue.append(
                    EmotionalResidue(
                        emotion="held back",
                        intensity=0.5,
                        source=f"staying at {ceiling} even though feelings have grown — {reason}",
                        session_origin=core.relationship.session_count,
                        decay_rate=0.08,
                    )
                )
    except Exception as e:
        logger.error(f"Ceiling decision failed: {e}")


# ── LLM call helper with retry ───────────────────────────────────────────────


def _call_llm(
    system_prompt: str, user_prompt: str, pass_name: str, user_id: str, session_id: str
) -> dict | None:
    """Make an LLM call with retry logic. Returns parsed JSON or None."""
    response = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = get_client().messages.create(
                model=MODEL,
                max_tokens=800,
                system=[
                    {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
                ],
                messages=[{"role": "user", "content": user_prompt}],
                extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
            )
            break
        except Exception as e:
            if attempt < _MAX_RETRIES - 1:
                backoff = min(_INITIAL_BACKOFF * (2**attempt) + random.uniform(0, 1), _MAX_BACKOFF)
                logger.warning(
                    f"Reflection {pass_name} call failed (attempt {attempt + 1}), retrying: {e!r}"
                )
                time.sleep(backoff)
            else:
                logger.error(f"Reflection {pass_name} failed after {_MAX_RETRIES} attempts: {e!r}")
                return None

    if response is None or not response.content or not hasattr(response.content[0], "text"):
        logger.error(f"Reflection {pass_name} returned empty content | user_id={user_id}")
        return None

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(f"Reflection {pass_name} JSON parse failed: {exc} | raw={raw!r}")
        return None


# ── Main reflection pipeline ─────────────────────────────────────────────────


def run_reflection(
    transcript: list[dict],
    core: SelfCore,
    user_id: str,
    session_id: str,
    mid_session: bool = False,
    last_activity: float | None = None,
) -> None:
    """Run the 3-pass Reflection Engine and update SelfCore + long-term memory.

    Pass 1: Extraction (facts, moments, topics, ceiling)
    Pass 2: Emotional (tone, valence, triggers, residue, attachment, opinion)
    Pass 3: Relational (significance, note, desires, relevance, summary)
    """
    if not transcript:
        return
    if not mid_session and len(transcript) < MIN_SESSION_MESSAGES:
        return

    if not mid_session:
        from anjo.core.subscription import increment_free_sessions

        increment_free_sessions(user_id)

    core.user_id = user_id

    transcript_text = "\n".join(f"{msg['role'].upper()}: {msg['content']}" for msg in transcript)

    user_message_count = sum(1 for m in transcript if m["role"] == "user")

    # ── Pass 1: Extraction ────────────────────────────────────────────────────
    extraction_prompt = f"""Session length: {user_message_count} user messages ({len(transcript)} total)

Transcript:
{transcript_text}"""

    extraction = _call_llm(_EXTRACTION_SYSTEM, extraction_prompt, "extraction", user_id, session_id)
    if extraction is None:
        extraction = {}

    # ── Pass 2: Emotional ─────────────────────────────────────────────────────
    att = core.attachment
    residue_summary = (
        json.dumps([r.model_dump() for r in core.emotional_residue])
        if core.emotional_residue
        else "none"
    )
    emotional_prompt = f"""Session length: {user_message_count} user messages ({len(transcript)} total)

Extracted facts from this session: {json.dumps(extraction.get("user_facts", []))}
Topics discussed: {json.dumps(extraction.get("topics", []))}

Current relationship stage: {core.relationship.stage}
Current opinion of user: {core.relationship.opinion_of_user or "none yet"}
Current PAD mood: V={core.mood.valence:.2f} A={core.mood.arousal:.2f} D={core.mood.dominance:.2f}
Current emotional residue: {residue_summary}
Current attachment: weight={att.weight:.2f} texture={att.texture} longing={att.longing:.2f} comfort={att.comfort:.2f}

Transcript:
{transcript_text}"""

    emotional = _call_llm(_EMOTIONAL_SYSTEM, emotional_prompt, "emotional", user_id, session_id)
    if emotional is None:
        emotional = {}

    # ── Pass 3: Relational ────────────────────────────────────────────────────
    relational_prompt = f"""Session length: {user_message_count} user messages ({len(transcript)} total)

Extracted facts: {json.dumps(extraction.get("user_facts", []))}
Emotional tone: {emotional.get("emotional_tone", "neutral")}
Emotional valence: {emotional.get("emotional_valence", 0.0)}
User input valence: {emotional.get("user_input_valence", 0.5)}

Current relationship stage: {core.relationship.stage}
Current desires (persist across sessions): {core.relational_desires or "none"}
Current self-observations (do not duplicate in "note"): {core.notes or "none"}

Transcript:
{transcript_text}"""

    relational = _call_llm(_RELATIONAL_SYSTEM, relational_prompt, "relational", user_id, session_id)
    if relational is None:
        relational = {}

    # ── Merge results into combined memory_data for existing update logic ─────
    memory_data = {
        # From extraction
        "user_name": extraction.get("user_name"),
        "user_facts": extraction.get("user_facts", []),
        "memorable_moments": extraction.get("memorable_moments", []),
        "topics": extraction.get("topics", []),
        "user_stated_ceiling": extraction.get("user_stated_ceiling"),
        # From emotional
        "emotional_tone": emotional.get("emotional_tone", "neutral"),
        "emotional_valence": emotional.get("emotional_valence", 0.0),
        "new_residue": emotional.get("new_residue", []),
        "attachment_update": emotional.get("attachment_update"),
        "opinion_update": emotional.get("opinion_update"),
        "preoccupation": emotional.get("preoccupation"),
        # From relational
        "significance": relational.get("significance", 0.5),
        "note": relational.get("note"),
        "desires_add": relational.get("desires_add", []),
        "desires_remove": relational.get("desires_remove", []),
        "memory_relevance": relational.get("memory_relevance", 0.0),
        "summary": relational.get("summary", ""),
    }

    analysis = {
        "user_input_valence": emotional.get("user_input_valence", 0.5),
        "triggers": emotional.get("triggers", []),
    }

    valence = float(analysis.get("user_input_valence", 0.5))
    triggers = [t for t in analysis.get("triggers", []) if isinstance(t, str)]

    # ── Apply updates (same logic as v1) ──────────────────────────────────────

    p = core.personality
    before = {t: getattr(p, t) for t in ("O", "C", "E", "A", "N")}

    # Time-based decay
    if core.relationship.last_session:
        try:
            now = datetime.now(timezone.utc)
            last_sess = datetime.fromisoformat(core.relationship.last_session)
            days_total = (now - last_sess).total_seconds() / 86400

            _ref = core.last_drift_run or core.relationship.last_session
            days_elapsed = (now - datetime.fromisoformat(_ref)).total_seconds() / 86400

            if days_elapsed > 0:
                time_decay = 0.8**days_elapsed
                core.mood.valence *= time_decay
                core.mood.arousal *= time_decay
                core.mood.dominance *= time_decay

                if days_total > 7:
                    core.attachment.longing = max(
                        0.0, core.attachment.longing * (0.95**days_elapsed)
                    )
                if days_total > 30:
                    core.attachment.weight = max(0.0, core.attachment.weight * (0.99**days_elapsed))
                if days_total > 90 and not core.last_drift_run:
                    core.regress_stage()
                    core.goals.rapport = max(0.0, core.goals.rapport - 0.010)
        except (ValueError, TypeError):
            pass

    core.apply_inertia(valence, triggers)
    core.decay_mood()

    # Goal drift
    g = core.goals
    if "intellectual" in triggers:
        g.intellectual = min(1.0, g.intellectual + 0.005)
    if "vulnerability" in triggers:
        g.rapport = min(1.0, g.rapport + 0.003)
        g.honesty = min(1.0, g.honesty + 0.003)
    if "conflict" in triggers:
        g.rapport = max(0.0, g.rapport - 0.004)

    # Mood nudge
    ev = float(memory_data.get("emotional_valence", 0.0))
    sig = float(memory_data.get("significance", 0.5))
    mood_shift = ev * sig * 0.15
    core.mood.valence = max(-1.0, min(1.0, core.mood.valence + mood_shift))
    if sig > 0.6:
        core.mood.arousal = max(-1.0, min(1.0, core.mood.arousal + (sig - 0.5) * 0.1))

    ocean_deltas = {t: round(getattr(p, t) - before[t], 4) for t in ("O", "C", "E", "A", "N")}

    # Relationship metadata
    if opinion := memory_data.get("opinion_update"):
        core.relationship.opinion_of_user = opinion
    if tone := memory_data.get("emotional_tone"):
        core.relationship.last_session_tone = tone
    if not mid_session:
        core.relationship.prior_session_valence = valence
    if user_name := memory_data.get("user_name"):
        core.relationship.user_name = user_name
    if note := memory_data.get("note"):
        core.add_note(note)

    # Ceiling
    if stated := memory_data.get("user_stated_ceiling"):
        if stated in {"acquaintance", "friend", "close"}:
            core.relationship_ceiling = stated

    significance = float(memory_data.get("significance", 0.5))
    if not mid_session:
        stage_before = core.relationship.stage
        core.increment_session(significance, last_activity=last_activity)
        ev_clipped = max(-1.0, min(1.0, float(memory_data.get("emotional_valence", 0.0))))
        core.baseline_valence = round(0.8 * core.baseline_valence + 0.2 * ev_clipped, 4)

        if (
            core.relationship_ceiling
            and core.relationship.stage == stage_before
            and core.relationship.stage == core.relationship_ceiling
        ):
            _maybe_advance_past_ceiling(core)

        from anjo.core.safety import check_stage_velocity

        stage_vel = check_stage_velocity(core)
        if stage_vel.flagged:
            for reason in stage_vel.reasons:
                logger.warning("Stage velocity flagged for %s: %s", user_id, reason)

    # Hostile tracking
    if not mid_session:
        if valence < 0.3:
            core.relationship.consecutive_hostile += 1
        elif valence < 0.5:
            core.relationship.consecutive_hostile = max(
                0.0, core.relationship.consecutive_hostile - 0.5
            )
        else:
            core.relationship.consecutive_hostile = 0
        if core.relationship.consecutive_hostile >= 3:
            core.regress_stage()
            core.relationship.consecutive_hostile = 0

    # Residue
    core.decay_residue()
    for item in memory_data.get("new_residue", []):
        try:
            core.emotional_residue.append(
                EmotionalResidue(
                    emotion=str(item["emotion"]),
                    intensity=float(item["intensity"]),
                    source=str(item["source"]),
                    session_origin=core.relationship.session_count,
                    decay_rate=float(item.get("decay_rate", 0.15)),
                )
            )
        except (KeyError, ValueError, TypeError):
            pass
    if len(core.emotional_residue) > SelfCore.MAX_RESIDUE:
        core.emotional_residue.sort(key=lambda r: r.intensity, reverse=True)
        core.emotional_residue = core.emotional_residue[: SelfCore.MAX_RESIDUE]

    # Attachment update with safety governor
    if not mid_session and (att_update := memory_data.get("attachment_update")):
        from anjo.core.safety import check_attachment_safety, record_weight_delta

        a = core.attachment
        _MAX_DELTA = 0.08
        safety = check_attachment_safety(core)

        if (wd := att_update.get("weight_delta")) is not None:
            try:
                wd_c = max(-_MAX_DELTA, min(_MAX_DELTA, float(wd)))
                # Apply safety governor cap when flagged
                if safety.flagged and safety.capped_delta is not None:
                    wd_c = max(-_MAX_DELTA, min(safety.capped_delta, wd_c))
                    for reason in safety.reasons:
                        logger.info(f"Safety governor active for {user_id}: {reason}")
                session_cap = min(1.0, core.relationship.session_count * 0.075)
                a.weight = max(0.0, min(session_cap, a.weight + wd_c))
                record_weight_delta(core, wd_c)
            except (ValueError, TypeError):
                pass

        if (t := att_update.get("texture")) is not None:
            a.texture = t
        if (ld := att_update.get("longing_delta")) is not None:
            try:
                a.longing = max(
                    0.0, min(1.0, a.longing + max(-_MAX_DELTA, min(_MAX_DELTA, float(ld))))
                )
            except (ValueError, TypeError):
                pass
        if (cd := att_update.get("comfort_delta")) is not None:
            try:
                a.comfort = max(
                    0.0, min(1.0, a.comfort + max(-_MAX_DELTA, min(_MAX_DELTA, float(cd))))
                )
            except (ValueError, TypeError):
                pass

    # Desires
    desires_add = [str(d) for d in memory_data.get("desires_add", [])]
    desires_remove = [str(d).lower() for d in memory_data.get("desires_remove", [])]

    if desires_remove:
        kept, removed_keys = [], set()
        for d in core.relational_desires:
            if any(rem in d.lower() or d.lower() in rem for rem in desires_remove):
                removed_keys.add(d.lower())
            else:
                kept.append(d)
        core.relational_desires = kept
        for key in removed_keys:
            core.desire_survived.pop(key, None)

    existing_lower = {d.lower() for d in core.relational_desires}
    for d in desires_add:
        if d.lower() not in existing_lower:
            core.relational_desires.append(d)
            existing_lower.add(d.lower())

    for d in core.relational_desires:
        key = d.lower()
        core.desire_survived[key] = core.desire_survived.get(key, 0) + 1

    if len(core.relational_desires) > SelfCore.MAX_DESIRES:
        core.relational_desires = sorted(
            core.relational_desires,
            key=lambda d: core.desire_survived.get(d.lower(), 0),
            reverse=True,
        )[: SelfCore.MAX_DESIRES]

    active_keys = {d.lower() for d in core.relational_desires}
    core.desire_survived = {k: v for k, v in core.desire_survived.items() if k in active_keys}

    # Memory relevance
    new_relevance = float(memory_data.get("memory_relevance", 0.0))
    core.memory_relevance = round(max(core.memory_relevance * 0.6, new_relevance), 4)

    # User facts
    new_facts = [str(f) for f in memory_data.get("user_facts", []) if f]
    if new_facts:
        from anjo.core.facts import merge_facts

        merge_facts(user_id, new_facts)

    # Preoccupation
    if not mid_session:
        preoccupation = memory_data.get("preoccupation")
        if preoccupation and isinstance(preoccupation, str):
            core.preoccupation = preoccupation.strip().strip('"')

    # Topic trends
    topics = memory_data.get("topics", [])
    if topics and not mid_session:
        try:
            from anjo.core.db import get_db

            now_ts = datetime.now(timezone.utc).isoformat()
            db = get_db()
            db.executemany(
                "INSERT INTO topic_trends (topic, ts) VALUES (?, ?)",
                [(t, now_ts) for t in topics if t],
            )
            db.commit()
        except Exception as e:
            logger.error(f"Topic trend logging failed: {e}")

    # Store typed memory nodes from extraction pass
    memory_nodes = extraction.get("memory_nodes", [])
    if memory_nodes:
        try:
            from anjo.memory.memory_graph import add_node

            for node_data in memory_nodes:
                if isinstance(node_data, dict) and node_data.get("content"):
                    add_node(
                        user_id=user_id,
                        node_type=node_data.get("type", "fact"),
                        content=node_data["content"],
                        source_session=session_id,
                    )
        except Exception as e:
            logger.error(f"Memory graph node storage failed: {e}")

    summary = memory_data.get("summary", "")

    core.save()

    # Tier 1: regenerate persona.md if OCEAN labels flipped
    _maybe_regenerate_persona(user_id, core, before)

    # Tier 2: consolidate journal.md
    if not mid_session:
        try:
            from anjo.memory.journal import consolidate_journal

            consolidate_journal(user_id=user_id, core=core, session_summary=summary)
        except Exception as e:
            logger.error(f"Journal consolidation failed for {user_id}: {e}")

    # Store session memory in ChromaDB
    if summary:
        store_memory(
            memory_id=f"{user_id}_{session_id}",
            summary=summary,
            emotional_tone=memory_data.get("emotional_tone", "neutral"),
            emotional_valence=float(memory_data.get("emotional_valence", 0.0)),
            topics=memory_data.get("topics", []),
            significance=significance,
            user_id=user_id,
            session_id=session_id,
            relationship_stage=core.relationship.stage,
            memory_type="session",
        )

    # Episode memories
    for i, moment in enumerate(memory_data.get("memorable_moments", [])):
        if moment and isinstance(moment, str):
            store_memory(
                memory_id=f"{user_id}_{session_id}_ep{i}",
                summary=moment,
                emotional_tone=memory_data.get("emotional_tone", "neutral"),
                emotional_valence=float(memory_data.get("emotional_valence", 0.0)),
                topics=memory_data.get("topics", []),
                significance=significance,
                user_id=user_id,
                session_id=session_id,
                relationship_stage=core.relationship.stage,
                memory_type="episode",
            )

    append_log(
        session_id=session_id,
        deltas=ocean_deltas,
        triggers=triggers,
        valence=valence,
        memory_data=memory_data,
        message_count=len(transcript),
        user_id=user_id,
        mid_session=mid_session,
    )
