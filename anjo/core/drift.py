"""Background drift — daily state changes that happen without a user session.

Keeps Anjo alive between conversations: mood drifts toward her baseline,
longing builds during absence, inter_session_drift is updated continuously.

Safe to call frequently — rate-limited to once per 20 hours per user.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from anjo.core.logger import logger

_DATA_ROOT = Path(__file__).parent.parent.parent / "data"
_MIN_HOURS_BETWEEN_RUNS = 20


def apply_daily_drift(user_id: str) -> bool:
    """Apply one day of natural drift to a user's SelfCore.

    Returns True if drift was applied, False if skipped (too soon since last run
    or if user has an active session).
    """
    from anjo.core.self_core import SelfCore
    from anjo.dashboard.session_store import get_session

    # Skip if user has an active session — drift should not run mid-session
    # to avoid race conditions with the session's live SelfCore state.
    if get_session(user_id):
        return False

    core = SelfCore.load(user_id)
    now = datetime.now(timezone.utc)

    # Rate-limit: skip if drift ran less than 20 hours ago
    if core.last_drift_run:
        try:
            last = datetime.fromisoformat(core.last_drift_run)
            if (now - last).total_seconds() < _MIN_HOURS_BETWEEN_RUNS * 3600:
                return False
        except (ValueError, TypeError):
            pass

    core.last_drift_run = now.isoformat()

    # Nothing to drift until the first session has happened
    if not core.relationship.last_session:
        core.save()
        return True

    try:
        last_session = datetime.fromisoformat(core.relationship.last_session)
        days_since = (now - last_session).total_seconds() / 86400
    except (ValueError, TypeError):
        core.save()
        return True

    # ── Mood: drift toward baseline_valence, not toward zero ─────────────────
    # Each day pulls 5% closer to her resting state. After ~2 weeks she's
    # halfway back; a hostile session doesn't define her forever.
    core.mood.valence   = round(core.mood.valence   * 0.95 + core.baseline_valence * 0.05, 4)
    core.mood.arousal   = round(core.mood.arousal   * 0.92, 4)
    core.mood.dominance = round(core.mood.dominance * 0.97, 4)

    # ── Longing: builds during first 7 days of absence, then fades ───────────
    if days_since <= 7:
        core.attachment.longing = min(1.0, round(core.attachment.longing + 0.035, 4))
    else:
        core.attachment.longing = max(0.0, round(core.attachment.longing * 0.95, 4))

    # ── Attachment weight: slow erosion after 30 days ─────────────────────────
    if days_since > 30:
        core.attachment.weight = max(0.0, round(core.attachment.weight * 0.99, 4))

    # ── Stage regression: 90+ day absence ────────────────────────────────────
    if days_since > 90:
        core.regress_stage()
        core.goals.rapport = max(0.0, core.goals.rapport - 0.010)

    # ── inter_session_drift: how far current mood sits from resting state ─────
    core.inter_session_drift = round(core.mood.valence - core.baseline_valence, 4)

    core.save()

    # ── Layer 3: proactive outreach check ────────────────────────────────────
    # Runs after save so maybe_generate_outreach sees the final state.
    # maybe_generate_outreach will call core.save() again only if it writes a message.
    try:
        from anjo.core.outreach import maybe_generate_outreach
        # Reload core so outreach sees the freshly-saved state
        core = SelfCore.load(user_id)
        maybe_generate_outreach(user_id, core, days_since)
    except Exception as e:
        logger.error(f"Outreach check failed for {user_id}: {e}")

    return True


def run_drift_for_all_users() -> None:
    """Run drift for every known user. Called by the background scheduler."""
    try:
        from anjo.core.db import get_db
        rows = get_db().execute("SELECT user_id FROM users").fetchall()
        user_ids = [row["user_id"] for row in rows]
    except Exception as e:
        logger.error(f"Drift: failed to enumerate users from DB: {e}")
        return
    for user_id in user_ids:
        try:
            apply_daily_drift(user_id)
        except Exception as e:
            logger.error(f"Drift error for user {user_id}: {e}")


# AutoDream: fires once per user after 4 hours of idle silence.
# Rate-limited by checking `last_autodream` in SelfCore (stored as ISO timestamp).
_AUTODREAM_MIN_HOURS = 4


def run_autodream_for_all_users() -> None:
    """Run AutoDream consolidation for users idle >= 4 hours.

    AutoDream consolidates the JOURNAL.md — updates working memory narrative,
    prunes low-intensity emotional residue, normalizes temporal references.
    Skips users with active sessions and those who ran AutoDream recently.
    Called by the background drift watcher (hourly tick, per-user rate-limited).
    """
    try:
        from anjo.core.db import get_db
        rows = get_db().execute("SELECT user_id FROM users").fetchall()
        user_ids = [row["user_id"] for row in rows]
    except Exception as e:
        logger.error(f"AutoDream: failed to enumerate users: {e}")
        return

    from anjo.dashboard.session_store import get_session
    now = datetime.now(timezone.utc)

    for user_id in user_ids:
        # Skip users with active sessions
        if get_session(user_id):
            continue
        try:
            from anjo.core.self_core import SelfCore
            core = SelfCore.load(user_id)

            # Skip if AutoDream ran recently
            if core.last_autodream:
                try:
                    last = datetime.fromisoformat(core.last_autodream)
                    if (now - last).total_seconds() < _AUTODREAM_MIN_HOURS * 3600:
                        continue
                except (ValueError, TypeError):
                    pass

            # Skip if no sessions have happened yet
            if not core.relationship.last_session:
                continue

            from anjo.memory.journal import run_autodream
            if run_autodream(user_id):
                # Record the run timestamp on SelfCore
                core = SelfCore.load(user_id)
                core.last_autodream = now.isoformat()
                core.save()
        except Exception as e:
            logger.error(f"AutoDream error for user {user_id}: {e}")
