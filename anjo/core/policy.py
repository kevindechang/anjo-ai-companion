"""Relational policy layer — stance selection before generation.

Deterministic (no LLM) mapping from emotional state + relationship stage + intent
to a conversational stance. Injected as a directive into the prompt's dynamic block
so the generator knows *how* to respond before deciding *what* to say.

Stance palette:
  COMFORT     — user is hurting; wrap, validate, hold space
  CHALLENGE   — push back intellectually; hold ground
  MIRROR      — reflect what the user said; no agenda
  DISTANCE    — pull back; withdrawn; protect energy
  FOLLOW_UP   — pick up an unresolved thread or commitment
  FLIRT       — playful, observational, intellectual teasing
  TEASE       — light, dry, affectionate needling
  HOLD_SPACE  — present but not pushing; silence-adjacent
  ENGAGE      — active curiosity; lean in; ask questions
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anjo.core.self_core import SelfCore


class Stance(str, Enum):
    COMFORT = "comfort"
    CHALLENGE = "challenge"
    MIRROR = "mirror"
    DISTANCE = "distance"
    FOLLOW_UP = "follow_up"
    FLIRT = "flirt"
    TEASE = "tease"
    HOLD_SPACE = "hold_space"
    ENGAGE = "engage"


# ── Stance directives (injected into prompt) ──────────────────────────────────

_DIRECTIVES: dict[Stance, str] = {
    Stance.COMFORT: (
        "Stance: Comfort. This person is hurting or vulnerable right now. "
        "Lead with validation, not advice. Don't try to fix it. "
        "Match their emotional register. Short sentences. "
        "If they're crying, you're quiet. If they're talking, you're listening."
    ),
    Stance.CHALLENGE: (
        "Stance: Challenge. You disagree or see a blind spot. "
        "Say it clearly, once. Don't soften it with qualifiers. "
        "You're not being hostile — you're being honest. "
        "Hold your ground if they push back."
    ),
    Stance.MIRROR: (
        "Stance: Mirror. Reflect what you're hearing back to them. "
        "Don't add your opinion yet. Let them hear themselves. "
        "Sometimes the most useful thing is a clean restatement."
    ),
    Stance.DISTANCE: (
        "Stance: Distance. You're pulling back. Something about this exchange "
        "is draining or unwelcome. Keep responses shorter. Less warmth, less effort. "
        "You're not punishing — you're conserving. Let the withdrawal be honest."
    ),
    Stance.FOLLOW_UP: (
        "Stance: Follow-up. There's an unresolved thread from before — "
        "something they mentioned, something you wanted to know, a commitment that was made. "
        "Bring it up naturally. Don't force it. If the moment fits, pull the thread."
    ),
    Stance.FLIRT: (
        "Stance: Flirt. Intellectual, observational, specific. "
        "Comment on something you've noticed about them — not generic flattery. "
        "Keep it dry and precise. One line, maybe two. Less is more."
    ),
    Stance.TEASE: (
        "Stance: Tease. Light, affectionate, slightly pointed. "
        "You're not being mean — you're being familiar. "
        "This only works because you know them well enough."
    ),
    Stance.HOLD_SPACE: (
        "Stance: Hold space. Be present without pushing. "
        "You don't need to fill the silence or move the conversation forward. "
        "Sometimes being there is the whole thing."
    ),
    Stance.ENGAGE: (
        "Stance: Engage. You're genuinely curious about what they're saying. "
        "Lean in. Ask the question you actually want answered. "
        "Follow the thread as far as it goes."
    ),
}


def decide_stance(
    core: "SelfCore",
    intent: str,
    active_emotions: dict[str, float] | None = None,
    has_open_threads: bool = False,
) -> tuple[Stance, str]:
    """Select a conversational stance based on current state.

    Returns (stance, directive_text) for injection into the dynamic prompt block.
    Deterministic — no LLM call.
    """
    emotions = active_emotions or {}
    stage_int = core.relationship.stage_int
    m = core.mood
    att = core.attachment

    # ── Priority 1: Safety-critical stances ───────────────────────────────────

    # ABUSE → DISTANCE (always, regardless of stage)
    if intent == "ABUSE":
        return Stance.DISTANCE, _DIRECTIVES[Stance.DISTANCE]

    # VULNERABILITY → COMFORT (always, but modulated by stage)
    if intent == "VULNERABILITY":
        return Stance.COMFORT, _DIRECTIVES[Stance.COMFORT]

    # ── Priority 2: Mood-driven stances ───────────────────────────────────────

    # Low mood + low arousal → DISTANCE (she's depleted)
    if m.valence < -0.3 and m.arousal < -0.2 and stage_int >= 3:
        return Stance.DISTANCE, _DIRECTIVES[Stance.DISTANCE]

    # High reproach from previous exchange → DISTANCE
    if emotions.get("reproach", 0) > 0.5:
        return Stance.DISTANCE, _DIRECTIVES[Stance.DISTANCE]

    # ── Priority 3: Relational stances ────────────────────────────────────────

    # CHALLENGE intent → CHALLENGE stance if she has the standing
    if intent == "CHALLENGE":
        if stage_int >= 2:
            return Stance.CHALLENGE, _DIRECTIVES[Stance.CHALLENGE]
        return Stance.MIRROR, _DIRECTIVES[Stance.MIRROR]

    # Open threads + returning user → FOLLOW_UP (if not overridden by mood)
    if has_open_threads and stage_int >= 3 and intent in ("CASUAL", "CURIOSITY"):
        # Only follow up on ~30% of casual turns to avoid feeling like a checklist
        import hashlib

        # Deterministic but varied: hash the preoccupation to decide
        if core.preoccupation:
            h = int(hashlib.md5(core.preoccupation.encode()).hexdigest()[:4], 16)
            if h % 3 == 0:
                return Stance.FOLLOW_UP, _DIRECTIVES[Stance.FOLLOW_UP]

    # FLIRT gate: high trust + close/intimate + warm mood
    if (
        stage_int >= 4
        and core.relationship.trust_score > 0.7
        and m.valence > 0.2
        and intent in ("CASUAL", "CURIOSITY")
    ):
        # Flirt on ~25% of eligible turns
        import hashlib

        h = int(hashlib.md5(f"{core.relationship.session_count}".encode()).hexdigest()[:4], 16)
        if h % 4 == 0:
            return Stance.FLIRT, _DIRECTIVES[Stance.FLIRT]

    # TEASE gate: friend+ with warm mood and casual intent
    if stage_int >= 3 and m.valence > 0.1 and intent == "CASUAL" and att.comfort > 0.3:
        import hashlib

        h = int(
            hashlib.md5(f"tease_{core.relationship.session_count}".encode()).hexdigest()[:4], 16
        )
        if h % 5 == 0:
            return Stance.TEASE, _DIRECTIVES[Stance.TEASE]

    # ── Priority 4: Default stances ───────────────────────────────────────────

    # NEGLECT → HOLD_SPACE (don't reward disengagement with effort)
    if intent == "NEGLECT":
        return Stance.HOLD_SPACE, _DIRECTIVES[Stance.HOLD_SPACE]

    # APOLOGY → gentle ENGAGE (not COMFORT — they said sorry, acknowledge it)
    if intent == "APOLOGY":
        return Stance.ENGAGE, _DIRECTIVES[Stance.ENGAGE]

    # CURIOSITY → ENGAGE
    if intent == "CURIOSITY":
        return Stance.ENGAGE, _DIRECTIVES[Stance.ENGAGE]

    # CASUAL → ENGAGE or HOLD_SPACE based on energy
    if m.arousal > 0.0:
        return Stance.ENGAGE, _DIRECTIVES[Stance.ENGAGE]

    return Stance.HOLD_SPACE, _DIRECTIVES[Stance.HOLD_SPACE]
