"""ChromaDB wrapper — per-user collections for O(1) access and vector space isolation.

Each user gets two dedicated collections:
  sem_{user_id} — semantic embeddings (what happened)
  emo_{user_id} — emotional embeddings (how it felt)

This avoids the O(N) metadata pre-filter that a single global collection requires
before any nearest-neighbor search can begin.

Migration note: existing data in the legacy "semantic_memories" / "emotional_memories"
global collections is not automatically migrated. Run scripts/migration_v2.py to
move existing vectors into per-user collections before switching production over.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import chromadb

from anjo.core.crypto import decrypt_chroma, encrypt_chroma, scrub_pii
from anjo.memory.embedder import embed_emotional, embed_semantic

_DATA_ROOT = Path(__file__).parent.parent.parent / "data"

_client: chromadb.PersistentClient | None = None


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        path = _DATA_ROOT / "chroma_global"
        path.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(path))
    return _client


def _get_collections(user_id: str):
    """Return (semantic_collection, emotional_collection) scoped to this user."""
    client = _get_client()
    sem_name = f"sem_{user_id}"
    emo_name = f"emo_{user_id}"
    semantic = client.get_or_create_collection(sem_name)
    emotional = client.get_or_create_collection(emo_name)
    return semantic, emotional


def store_memory(
    memory_id: str,
    summary: str,
    emotional_tone: str,
    emotional_valence: float,
    topics: list[str],
    significance: float,
    user_id: str,
    session_id: str,
    relationship_stage: str,
    memory_type: str = "session",  # "session" | "episode"
) -> None:
    semantic_col, emotional_col = _get_collections(user_id)

    metadata = {
        "session_id": session_id,
        "user_id": user_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "emotional_tone": emotional_tone,
        "emotional_valence": float(emotional_valence),
        "topics": json.dumps(topics),
        "significance": float(significance),
        "relationship_stage": relationship_stage,
        "memory_type": memory_type,
    }

    # Compute embeddings from PII-scrubbed text to keep PII out of vectors.
    # Store the full (encrypted) summary as the document for accurate retrieval.
    scrubbed = scrub_pii(summary)
    sem_vec = embed_semantic(scrubbed)
    emo_vec = embed_emotional(scrubbed)

    encrypted_summary = encrypt_chroma(summary)
    semantic_col.upsert(
        ids=[memory_id], embeddings=[sem_vec], documents=[encrypted_summary], metadatas=[metadata]
    )
    emotional_col.upsert(
        ids=[memory_id], embeddings=[emo_vec], documents=[encrypted_summary], metadatas=[metadata]
    )


def get_last_session_summary(user_id: str) -> str | None:
    """Return the most recent session summary by timestamp, regardless of semantic relevance.
    Only returns session-level memories, not episode-level moments.
    """
    semantic_col, _ = _get_collections(user_id)
    results = semantic_col.get(
        where={"memory_type": "session"},
        include=["documents", "metadatas"],
    )
    if not results["documents"]:
        return None
    pairs = list(zip(results["metadatas"], results["documents"]))
    if not pairs:
        return None
    pairs.sort(key=lambda x: x[0].get("timestamp", ""), reverse=True)
    return decrypt_chroma(pairs[0][1])


def _recency_weight(timestamp: str) -> float:
    """1.0 for today, decays to 0.5 over 30 days, floor at 0.4."""
    try:
        ts = datetime.fromisoformat(timestamp)
        days_ago = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        return max(0.4, 1.0 - days_ago / 60.0)
    except Exception:
        return 0.7


def query_memories(message: str, user_id: str, n_results: int = 4) -> list[tuple[float, str]]:
    """Query both session summaries and episode memories with recency boost.

    Returns up to n_results (score, memory_text) tuples, sorted by descending score.
    Scores are in [0.0, 1.0+] (recency-weighted similarity + episode bonus).

    Prioritises:
    - Episodes (specific moments) over session summaries for the same topic
    - Recent over old at equal similarity

    Callers use scores for skeptical memory framing:
    - score >= 0.7: high certainty — "I recall that..."
    - score 0.5-0.7: medium certainty — "I have a sense that..."
    - score < 0.5: omit (noise)
    """
    semantic_col, emotional_col = _get_collections(user_id)

    # Count memories in this user's collection to avoid over-querying
    user_count = semantic_col.count()
    if user_count == 0:
        return []

    sem_vec = embed_semantic(message)
    emo_vec = embed_emotional(message)

    k = min(n_results + 4, user_count)

    sem_results = semantic_col.query(
        query_embeddings=[sem_vec],
        n_results=k,
        include=["documents", "distances", "metadatas"],
    )
    emo_results = emotional_col.query(
        query_embeddings=[emo_vec],
        n_results=k,
        include=["documents", "distances", "metadatas"],
    )

    # Build scored candidates: score = (1 - distance/2) * recency_weight
    # Episodes get a small bonus (they are more specific and precise)
    candidates: dict[str, tuple[float, str]] = {}  # id → (score, doc)

    for results in (sem_results, emo_results):
        if not results.get("ids") or not results["ids"] or not results["ids"][0]:
            continue
        for mem_id, doc, dist, meta in zip(
            results["ids"][0],
            results["documents"][0],
            results["distances"][0],
            results["metadatas"][0],
        ):
            similarity = max(0.0, 1.0 - dist / 2.0)
            recency = _recency_weight(meta.get("timestamp", ""))
            episode_bonus = 0.05 if meta.get("memory_type") == "episode" else 0.0
            score = similarity * recency + episode_bonus
            if mem_id not in candidates or score > candidates[mem_id][0]:
                candidates[mem_id] = (score, decrypt_chroma(doc))

    ranked = sorted(candidates.values(), key=lambda x: -x[0])
    return ranked[:n_results]
