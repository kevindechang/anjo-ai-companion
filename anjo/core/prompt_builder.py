"""System prompt builder for the AI companion.

This module is responsible for constructing the system prompt that is sent to the
LLM on every turn. It combines the companion's personality state (SelfCore) with
retrieved memories and ephemeral per-turn state to produce two prompt blocks:

  Block 1 (static)  — Persona / identity. Stable; prompt-cache-friendly.
  Block 2 (dynamic) — Journal, mood, retrieved memories, live session context.

The two-block structure is designed for Anthropic's prompt caching:
  - Block 1 is the same across most turns (cache hit ~95%+).
  - Block 2 is rebuilt every turn (never cached).

Three-Tier Memory Architecture (reference for implementors):
  Tier 1 — PERSONA.md  : per-user static file, loaded as static block.
  Tier 2 — JOURNAL.md  : per-user rolling working memory, dynamic block.
  Tier 3 — ChromaDB    : on-demand retrieval, injected into dynamic block.
"""
from __future__ import annotations

from anjo.core.self_core import SelfCore


def build_system_prompt(
    core: SelfCore,
    retrieved_memories: list[tuple[float, str]] | list[str] | None = None,
    active_emotions: dict | None = None,
    tz_offset: int = 0,
    user_turn_count: int = 0,
    seed_len: int = 0,
    user_facts: list[str] | None = None,
    trending_topics: list[str] | None = None,
    stance_directive: str = "",
) -> tuple[str, str]:
    """Build the system prompt for the AI companion.

    Returns (static_block, dynamic_block).

    TODO: Implement your own system prompt logic here.

    This function receives:
      - core            : SelfCore instance with personality, mood, relationship state
      - retrieved_memories : relevant past memories from ChromaDB (may be None)
      - active_emotions : OCC emotions from the current turn's appraisal (may be None)
      - tz_offset       : user's timezone offset in minutes from UTC
      - user_turn_count : number of user messages in the current session
      - seed_len        : number of seed messages prepended for continuity
      - user_facts      : known facts about the user (from facts store)
      - trending_topics : trending topics from recent sessions
      - stance_directive : optional relational stance override string

    Return a tuple of two strings:
      - static_block  : stable prompt text (persona, identity, behavioral guidelines).
                        Cache this block — it rarely changes.
      - dynamic_block : per-turn prompt text (current mood, memories, session state).
                        Never cache — rebuild each turn.

    Example structure for static_block:
      "You are [companion name], an AI companion with a distinct personality.
       [Personality description derived from core.personality OCEAN traits]
       [Behavioral guidelines, voice, tone]"

    Example structure for dynamic_block:
      "Current session state:
       [Mood directives from core.mood PAD values]
       [Active emotions from appraisal]
       [Retrieved memories with confidence framing]
       [Relationship context: stage, session count, last seen]
       [Onboarding note if first session]"

    See docs/anjo-technical.md for the full architecture reference.
    """
    raise NotImplementedError(
        "Implement your own system prompt builder. "
        "See the docstring and docs/anjo-technical.md for guidance."
    )
