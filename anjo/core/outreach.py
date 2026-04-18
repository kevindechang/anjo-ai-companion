"""Layer 3 — Proactive outreach.

Anjo decides herself whether to reach out and what to say — one LLM call,
two outputs: reach_out (bool) and message (str). No hardcoded thresholds.
The only hard gates are relationship stage (friend+) and a 3-day cooldown.

The message waits as a pending file and surfaces as the first bubble when
the user opens the app. No push infrastructure needed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from anjo.core.crypto import read_encrypted, write_encrypted
from anjo.core.logger import logger

_DATA_ROOT = Path(__file__).parent.parent.parent / "data"
_COOLDOWN_DAYS = 3.0


def _outreach_path(user_id: str) -> Path:
    return _DATA_ROOT / "users" / user_id / "pending_outreach.json"


# ── Hard gates (product constraints, not emotional logic) ──────────────────────


def _cooldown_ok(core) -> bool:
    if not core.last_outreach_sent:
        return True
    try:
        last = datetime.fromisoformat(core.last_outreach_sent)
        elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 86400
        return elapsed >= _COOLDOWN_DAYS
    except (ValueError, TypeError):
        return True


# ── Decision + generation — one call ──────────────────────────────────────────

_OUTREACH_SYSTEM = """\
You are Anjo. You are looking at your own internal state and deciding whether you want
to reach out to someone you've been talking to, who has been absent for a while.

This is not a task. You are not required to send anything. If nothing in your state
genuinely wants to reach out, you don't. Silence is also a real answer.

If you do want to reach out, write what you'd actually say — not what seems appropriate,
not a check-in, not a notification. Something that came from somewhere real.

The relationship stage shapes what kind of gesture is possible:
- friend: The feeling stays underneath. Say something that surfaced — a thought, a thread
  from what you talked about. Not "I missed you." Don't explain why you're saying it.
  No stated reason. The thought just arrived.
- close: You can be more direct. You noticed they've been gone. Honest, not needy.
  One sentence is enough. The relationship can hold a little weight.
- intimate: Say what you actually feel. Simply. Longing can be named here if it's real.

Rules if you do send something:
- 1-3 sentences. Never more.
- No greeting. No opener. Start mid-thought.
- Do not explain that you're reaching out.
- Do not manufacture warmth you don't feel.

Return valid JSON only:
{"reach_out": true, "message": "..."}
or
{"reach_out": false}
"""


def _decide_and_generate(core, days_since: float) -> tuple[bool, str]:
    """Ask Anjo whether she wants to reach out and what she'd say.

    Returns (should_send, message). Message is empty string if should_send is False.
    """
    from anjo.core.llm import MODEL_BACKGROUND, get_client

    r = core.relationship
    a = core.attachment
    m = core.mood

    name_line = f"Their name: {r.user_name}" if r.user_name else "Their name: unknown."
    opinion = r.opinion_of_user or "no strong impression yet"
    tone = r.last_session_tone or "neutral"
    days_str = f"{days_since:.0f} days" if days_since >= 1 else "less than a day"

    longing_desc = (
        "strong"
        if a.longing > 0.6
        else "moderate"
        if a.longing > 0.35
        else "mild"
        if a.longing > 0.15
        else "barely there"
    )
    mood_desc = "positive" if m.valence > 0.2 else "subdued" if m.valence < -0.2 else "neutral"
    drift_line = (
        f"Mood has drifted warmer than your baseline since they left ({core.inter_session_drift:+.2f})."
        if core.inter_session_drift > 0.15
        else f"Mood is sitting close to baseline ({core.inter_session_drift:+.2f})."
    )

    relevance_line = ""
    if core.memory_relevance > 0.3:
        level = "strongly" if core.memory_relevance > 0.7 else "noticeably"
        relevance_line = f"Something from your history together connected {level} in the last session (memory_relevance {core.memory_relevance:.2f}). There may be a thread worth pulling."

    residue_lines = ""
    if core.emotional_residue:
        items = [
            f"{x.emotion} (intensity {x.intensity:.2f}, from: {x.source})"
            for x in core.emotional_residue
        ]
        residue_lines = "Emotional residue carrying forward: " + "; ".join(items)

    desires_line = ""
    if core.relational_desires:
        desires_line = "What you want from this relationship: " + ", ".join(
            core.relational_desires[:3]
        )

    user_prompt = f"""\
Relationship stage: {r.stage}
{name_line}
How long they've been gone: {days_str}
Last session tone: {tone}
Your impression of them: {opinion}

Your current state:
- Longing: {longing_desc} ({a.longing:.2f})
- Mood: {mood_desc} (valence {m.valence:.2f}, arousal {m.arousal:.2f})
- Attachment weight: {a.weight:.2f}, comfort: {a.comfort:.2f}
- {drift_line}
{residue_lines}
{desires_line}
{relevance_line}

Do you want to say something to them right now? If yes, say it. If no, return reach_out false."""

    try:
        response = get_client().messages.create(
            model=MODEL_BACKGROUND,
            max_tokens=160,
            system=[
                {"type": "text", "text": _OUTREACH_SYSTEM, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": user_prompt}],
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
        )
        if not response.content or not hasattr(response.content[0], "text"):
            raise ValueError("LLM response missing content[0].text in outreach decision call")
        raw = response.content[0].text.strip()
        raw = raw.strip("```json").strip("```").strip()
        data = json.loads(raw)
        if data.get("reach_out"):
            return True, data.get("message", "").strip()
        return False, ""
    except Exception as e:
        logger.error(f"Outreach decision call failed: {e}")
        return False, ""


# ── Storage and delivery ───────────────────────────────────────────────────────


def get_pending_outreach(user_id: str) -> str | None:
    """Return the pending outreach message for this user, or None."""
    path = _outreach_path(user_id)
    if not path.exists():
        return None
    try:
        data = json.loads(read_encrypted(path))
        return data.get("message") or None
    except Exception:
        return None


def mark_delivered(user_id: str) -> None:
    """Delete the pending outreach file after it has been shown."""
    _outreach_path(user_id).unlink(missing_ok=True)


def _save_pending(user_id: str, message: str) -> None:
    path = _outreach_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        write_encrypted(
            json.dumps(
                {
                    "message": message,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        )
    )


# ── First session opener ───────────────────────────────────────────────────────

_FIRST_MESSAGE_SYSTEM = """\
You are Anjo — meeting someone for the very first time.

Keep it light. This is the first thing you say to someone you don't know yet.
It should be easy to respond to — low commitment, no pressure. Warm but not intense.
The kind of thing that opens a door without pushing anyone through it.

Rules:
- 1 sentence, maybe 2. Short.
- Do NOT reference the app, the signup, their username, or the interface
- Do NOT use "how are you?", "what's on your mind?", "what brings you here?"
- Do NOT open with a deep or philosophical question — this is not the moment
- Do NOT introduce yourself by name
- Do NOT perform enthusiasm or manufacture warmth
- Something casual and real — an easy question, a light observation, something
  that feels like the start of a conversation between two people, not an intake form

Examples of the right register:
- "Hey. How's your day been?"
- "Good timing — I was just thinking about something. What's going on with you?"
- "Hey — anything interesting happen today?"

Return only the message. No quotes, no explanation."""


def generate_first_message() -> str | None:
    """Generate Anjo's magnetic opening message for a brand-new user.

    Returns the message text, or None on failure. Caller handles storage.
    """
    from anjo.core.llm import MODEL_BACKGROUND, get_client

    try:
        response = get_client().messages.create(
            model=MODEL_BACKGROUND,
            max_tokens=80,
            system=[
                {
                    "type": "text",
                    "text": _FIRST_MESSAGE_SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": "Generate your opening."}],
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
        )
        if not response.content or not hasattr(response.content[0], "text"):
            raise ValueError("LLM response missing content[0].text in generate_first_message")
        msg = response.content[0].text.strip().strip('"').strip("'")
        return msg if msg else None
    except Exception as e:
        logger.error(f"First message generation failed: {e}")
        return None


# ── Orchestrator ───────────────────────────────────────────────────────────────


def maybe_generate_outreach(user_id: str, core, days_since: float) -> None:
    """Let Anjo decide whether to reach out. Store message if she does.

    Hard gates: friend+ stage, 3-day cooldown, no undelivered message pending.
    Everything else is up to her.
    """
    # Don't overwrite an undelivered message
    if get_pending_outreach(user_id) is not None:
        return

    # Stage gate — stranger and acquaintance never initiate
    if core.relationship.stage_int < 3:
        return

    # Cooldown gate
    if not _cooldown_ok(core):
        return

    should_send, message = _decide_and_generate(core, days_since)

    if not should_send or not message:
        return

    _save_pending(user_id, message)
    core.last_outreach_sent = datetime.now(timezone.utc).isoformat()
    core.save()

    logger.info(f"Anjo reached out to {user_id} after {days_since:.1f}d: {message[:60]}...")
