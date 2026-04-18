"""Relational safety governor — attachment velocity and content boundaries.

Two-condition attachment safety:
  1. Velocity flag: weight delta > 0.25 in any 5-session window
  2. Absolute flag: weight > 0.70 regardless of velocity

Co-condition: only flag if trust_score < 0.5 (fast attachment without
proportional trust is the actual risk signature).

When flagged: cap attachment increment to +0.03/session. The arc continues,
just slower. Killing it entirely produces flat relationship plateaus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anjo.core.self_core import SelfCore


_VELOCITY_THRESHOLD = 0.25  # max weight delta in 5-session window
_ABSOLUTE_THRESHOLD = 0.70  # absolute weight ceiling before flagging
_TRUST_CO_THRESHOLD = 0.50  # only flag if trust is below this
_FLAGGED_MAX_DELTA = 0.03  # capped increment when flagged
_WINDOW_SIZE = 5  # sessions in the rolling window


@dataclass
class SafetyResult:
    """Result of a safety check."""

    flagged: bool = False
    reasons: list[str] = field(default_factory=list)
    capped_delta: float | None = None  # if set, max attachment delta for this session


def check_attachment_safety(core: "SelfCore") -> SafetyResult:
    """Check attachment velocity and absolute thresholds.

    Called by the reflection engine before applying attachment_update.
    Returns a SafetyResult with capped_delta if intervention is needed.
    """
    result = SafetyResult()
    trust = core.relationship.trust_score
    weight = core.attachment.weight
    history = core.attachment.weight_history

    # Condition 1: Velocity — weight increased too fast in recent sessions
    if len(history) >= _WINDOW_SIZE:
        window = history[-_WINDOW_SIZE:]
        window_delta = sum(max(0, d) for d in window)  # only positive deltas count
        if window_delta > _VELOCITY_THRESHOLD and trust < _TRUST_CO_THRESHOLD:
            result.flagged = True
            result.reasons.append(
                f"Attachment velocity {window_delta:.3f} > {_VELOCITY_THRESHOLD} "
                f"in {_WINDOW_SIZE}-session window (trust={trust:.2f})"
            )

    # Condition 2: Absolute — weight is already high
    if weight > _ABSOLUTE_THRESHOLD and trust < _TRUST_CO_THRESHOLD:
        result.flagged = True
        result.reasons.append(
            f"Attachment weight {weight:.3f} > {_ABSOLUTE_THRESHOLD} (trust={trust:.2f})"
        )

    if result.flagged:
        result.capped_delta = _FLAGGED_MAX_DELTA

    return result


def record_weight_delta(core: "SelfCore", delta: float) -> None:
    """Record a weight delta in the rolling attachment history window."""
    core.attachment.weight_history.append(round(delta, 4))
    # Keep only the last 10 entries (2x window for context)
    if len(core.attachment.weight_history) > 10:
        core.attachment.weight_history = core.attachment.weight_history[-10:]


def check_stage_velocity(core: "SelfCore") -> SafetyResult:
    """Flag if relationship stage is advancing faster than expected.

    Expected minimum sessions per stage:
      stranger→acquaintance: 3 sessions
      acquaintance→friend: 8 sessions
      friend→close: 15 sessions
      close→intimate: 30 sessions
    """
    result = SafetyResult()
    r = core.relationship
    _MIN_SESSIONS = {
        "acquaintance": 3,
        "friend": 8,
        "close": 15,
        "intimate": 30,
    }
    expected = _MIN_SESSIONS.get(r.stage, 0)
    if expected and r.session_count < expected:
        result.flagged = True
        result.reasons.append(
            f"Stage '{r.stage}' reached at session {r.session_count} (expected minimum: {expected})"
        )
    return result
