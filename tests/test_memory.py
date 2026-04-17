"""
Memory system integration tests — LongMemEval-inspired.

Validates the full store → query → recall pipeline:
- Facts stored in ChromaDB are retrievable above the 0.5 certainty threshold
- User isolation: memories never leak across users
- Ranking: the most relevant memory surfaces first when multiple exist
- Episode bonus: specific moments outrank session summaries for same content
- Last-session anchor: get_last_session_summary returns the most recent one
"""
from __future__ import annotations

import uuid
import pytest


@pytest.fixture(autouse=True)
def isolate_chroma(tmp_path, monkeypatch):
    """Redirect ChromaDB to a throwaway temp dir and reset the global client."""
    import anjo.memory.long_term as lt
    monkeypatch.setattr(lt, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(lt, "_client", None)
    yield
    monkeypatch.setattr(lt, "_client", None)


def _store(summary: str, topics: list[str], user_id: str, memory_type: str = "session") -> str:
    from anjo.memory.long_term import store_memory
    mem_id = str(uuid.uuid4())
    store_memory(
        memory_id=mem_id,
        summary=summary,
        emotional_tone="neutral",
        emotional_valence=0.0,
        topics=topics,
        significance=0.8,
        user_id=user_id,
        session_id="test-session",
        relationship_stage="acquaintance",
        memory_type=memory_type,
    )
    return mem_id


# ── Basic recall ──────────────────────────────────────────────────────────────

def test_stored_fact_is_retrievable():
    """A fact stored in memory should come back when queried on the same topic."""
    from anjo.memory.long_term import query_memories

    user = "user_basic"
    _store("The user is a software engineer who loves hiking on weekends.", ["work", "hiking"], user)

    results = query_memories("What does the user do for work?", user)

    assert results, "Expected at least one result"
    top_score, top_text = results[0]
    assert "software engineer" in top_text
    assert top_score >= 0.5, f"Score {top_score:.3f} below certainty threshold"


def test_no_memories_returns_empty():
    from anjo.memory.long_term import query_memories

    results = query_memories("tell me about yourself", "ghost_user")
    assert results == []


# ── User isolation ─────────────────────────────────────────────────────────────

def test_memories_do_not_leak_across_users():
    """Querying as a different user must return nothing from another user's memories."""
    from anjo.memory.long_term import query_memories

    _store("Alice lives in Tokyo and works as a nurse.", ["location", "job"], "user_alice")

    results = query_memories("Where does the user live?", "user_bob")
    assert results == [], "Bob should see none of Alice's memories"


# ── Ranking ────────────────────────────────────────────────────────────────────

def test_most_relevant_memory_ranks_first():
    """When multiple memories exist, the one closest to the query topic should rank highest."""
    from anjo.memory.long_term import query_memories

    user = "user_rank"
    _store("The user enjoys cooking Italian food and trying new recipes.", ["cooking", "food"], user)
    _store("The user recently started learning Spanish on Duolingo.", ["language", "learning"], user)
    _store("The user has a golden retriever named Max.", ["pets", "dog"], user)

    results = query_memories("Does the user have any pets?", user)

    assert results, "Expected results"
    _, top_text = results[0]
    assert "Max" in top_text or "golden retriever" in top_text, (
        f"Expected the pet memory at top, got: {top_text[:80]}"
    )


# ── Episode bonus ──────────────────────────────────────────────────────────────

def test_episode_scores_higher_than_session_for_same_content():
    """Episode-type memories should outscore session-type memories on the same content."""
    from anjo.memory.long_term import query_memories

    user = "user_episode"
    content = "The user cried while watching a sad movie about a dog."
    _store(content, ["emotion", "movie"], user, memory_type="session")
    _store(content, ["emotion", "movie"], user, memory_type="episode")

    results = query_memories("Tell me about an emotional moment.", user)

    assert len(results) >= 2
    top_score = results[0][0]
    second_score = results[1][0]
    assert top_score >= second_score
    # Episode bonus is 0.05 — scores should differ
    assert top_score > second_score, "Episode memory should score strictly higher"


# ── Last-session anchor ────────────────────────────────────────────────────────

def test_get_last_session_summary_returns_most_recent():
    """get_last_session_summary should return the latest session-type memory."""
    from anjo.memory.long_term import store_memory, get_last_session_summary
    import time

    user = "user_anchor"

    store_memory(
        memory_id=str(uuid.uuid4()),
        summary="First session: user introduced themselves.",
        emotional_tone="warm", emotional_valence=0.3,
        topics=["intro"], significance=0.5,
        user_id=user, session_id="s1",
        relationship_stage="stranger", memory_type="session",
    )

    # Small sleep to ensure distinct timestamps
    time.sleep(0.05)

    store_memory(
        memory_id=str(uuid.uuid4()),
        summary="Second session: user talked about their fear of flying.",
        emotional_tone="tense", emotional_valence=-0.4,
        topics=["fear", "travel"], significance=0.7,
        user_id=user, session_id="s2",
        relationship_stage="acquaintance", memory_type="session",
    )

    summary = get_last_session_summary(user)
    assert summary is not None
    assert "fear of flying" in summary, f"Expected second session, got: {summary[:80]}"


def test_get_last_session_summary_ignores_episodes():
    """get_last_session_summary should only look at session-type memories."""
    from anjo.memory.long_term import store_memory, get_last_session_summary
    import time

    user = "user_anchor2"

    store_memory(
        memory_id=str(uuid.uuid4()),
        summary="Session summary: overall a good conversation.",
        emotional_tone="warm", emotional_valence=0.5,
        topics=["general"], significance=0.6,
        user_id=user, session_id="s1",
        relationship_stage="acquaintance", memory_type="session",
    )

    time.sleep(0.05)

    # A later episode — should NOT be returned by get_last_session_summary
    store_memory(
        memory_id=str(uuid.uuid4()),
        summary="Specific moment: user laughed at a joke about penguins.",
        emotional_tone="playful", emotional_valence=0.8,
        topics=["humor"], significance=0.3,
        user_id=user, session_id="s1",
        relationship_stage="acquaintance", memory_type="episode",
    )

    summary = get_last_session_summary(user)
    assert summary is not None
    assert "good conversation" in summary
    assert "penguin" not in summary


# ── Score thresholds ───────────────────────────────────────────────────────────

def test_query_on_matching_topic_scores_above_threshold():
    """Topically relevant memories should clear the 0.5 certainty threshold."""
    from anjo.memory.long_term import query_memories

    user = "user_threshold"
    _store(
        "The user mentioned their mother passed away last year and they are still grieving.",
        ["grief", "family", "loss"],
        user,
    )

    results = query_memories("How is the user dealing with their loss?", user)
    assert results, "Expected at least one result"
    score, _ = results[0]
    assert score >= 0.5, f"Score {score:.3f} is below the 0.5 threshold — memory would be silently dropped"
