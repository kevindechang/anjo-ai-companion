"""Reflection Engine — analyzes conversation transcripts and updates SelfCore.

The reflection engine is called after each conversation session ends. It reads the
full transcript and the current SelfCore state, runs analysis, and writes back
updated personality/relationship state to disk.

This is the core "learning" mechanism of the companion — how it grows over time
based on interactions with the user.

Suggested 3-pass pipeline architecture:
  Pass 1 — Extraction  : facts, user name, memorable moments, topics
  Pass 2 — Emotional   : emotional tone, valence, triggers, residue, attachment
  Pass 3 — Relational  : session significance, notes, desires, summary

Each pass uses a focused LLM call with its own system prompt and output schema.
Pass 1 output feeds Pass 2 context, etc.

See docs/anjo-technical.md for the full architecture reference.
"""
from __future__ import annotations

from anjo.core.self_core import SelfCore

MIN_SESSION_MESSAGES = 4


def run_reflection(
    transcript: list[dict],
    core: SelfCore,
    user_id: str,
    session_id: str,
    mid_session: bool = False,
    last_activity: float | None = None,
) -> None:
    """Analyze the conversation transcript and update the companion's personality state.

    Called after each conversation session ends (or mid-session every N messages).

    TODO: Implement your own reflection logic here.

    Args:
        transcript   : List of message dicts from the conversation.
                       Each dict has keys: "role" ("user" | "assistant"), "content" (str).
        core         : Current SelfCore personality state (loaded from disk before this call).
        user_id      : The user's ID string.
        session_id   : The session's ID string.
        mid_session  : If True, this is a lightweight mid-session pass (every ~20 messages).
                       Skip session increment, skip long-term memory writes.
        last_activity: Unix timestamp of last user message, used to timestamp the session.

    What this function should do:
      1. Parse the transcript to extract facts, emotional tone, significant moments.
      2. Update core.relationship (stage, trust, opinion, user_name, session_count).
      3. Update core.personality (OCEAN traits via core.apply_inertia()).
      4. Update core.mood (PAD values via core.decay_mood() + mood nudges).
      5. Update core.attachment (weight, texture, longing, comfort).
      6. Update core.notes, core.emotional_residue, core.relational_desires.
      7. Call core.save() to persist the updated state.
      8. Store session summary in ChromaDB via anjo.memory.long_term.store_memory().
      9. Update the journal (anjo.memory.journal.consolidate_journal()).
      10. Log the reflection (anjo.reflection.log.append_log()).

    Key helpers available on SelfCore:
      - core.apply_inertia(valence, triggers) — update OCEAN traits
      - core.decay_mood()                     — decay PAD 20% toward neutral
      - core.decay_residue()                  — decay emotional residue
      - core.increment_session(significance)  — advance relationship stage
      - core.add_note(note)                   — append behavioral self-observation
      - core.save()                           — persist to disk

    Returns:
        None (mutates core in place and saves to disk).
    """
    if not transcript:
        return
    if not mid_session and len(transcript) < MIN_SESSION_MESSAGES:
        return

    raise NotImplementedError(
        "Implement your own reflection engine. "
        "See the docstring and docs/anjo-technical.md for guidance."
    )
