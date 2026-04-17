"""Typed memory graph — structured, queryable memory nodes alongside ChromaDB.

Node types:
  fact          — concrete details: "works as a nurse", "sister named Maya"
  preference    — likes/dislikes: "hates horror movies"
  commitment    — promises/plans: "said they'd send the link"
  thread        — unresolved topics: "hasn't talked to their sister in years"
  contradiction — conflicting info: "said Seoul but also mentioned London"

Stored in SQLite `memory_graph` table. Supports:
  - Typed queries (get all facts, all open threads, etc.)
  - Supersession (new fact replaces old fact in same category)
  - Contradiction detection (two active facts with conflicting content)
  - User-facing deletion (semantic/fact nodes granular, emotional by date range)
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from anjo.core.crypto import encrypt_db, decrypt_db
from anjo.core.db import get_db


# ── Models ────────────────────────────────────────────────────────────────────

class MemoryNode(BaseModel):
    """A typed memory node in the user's relationship graph."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    user_id: str
    node_type: str  # fact | preference | commitment | thread | contradiction
    content: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_session: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    superseded_at: Optional[str] = None
    related_nodes: list[str] = Field(default_factory=list)  # IDs of related nodes

    @property
    def is_active(self) -> bool:
        return self.superseded_at is None


VALID_NODE_TYPES = {"fact", "preference", "commitment", "thread", "contradiction"}


# ── Category detection (for supersession) ─────────────────────────────────────

_CATEGORIES: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"\b(work|job|career|profession|employ|engineer|developer|doctor|nurse|teacher|"
        r"student|manager|designer|scientist|lawyer|analyst|architect|chef|"
        r"pilot|therapist|consultant|writer|artist|musician|programmer|coder|"
        r"intern|freelanc)\b", re.I),
     "occupation"),
    (re.compile(
        r"\b(live|lives|living|reside|based in|moved to|relocated|settled in)\b", re.I),
     "location"),
    (re.compile(
        r"\b(married|single|divorced|dating|relationship|partner|girlfriend|boyfriend|"
        r"wife|husband|engaged|widowed|separated|broke up)\b", re.I),
     "relationship_status"),
    (re.compile(
        r"\b(study|studying|school|university|college|major|degree|graduate|graduated|"
        r"phd|masters|bachelors)\b", re.I),
     "education"),
]


def _detect_category(content: str) -> str | None:
    for pattern, cat in _CATEGORIES:
        if pattern.search(content):
            return cat
    return None


# ── CRUD operations ───────────────────────────────────────────────────────────

def add_node(
    user_id: str,
    node_type: str,
    content: str,
    confidence: float = 1.0,
    source_session: str = "",
    related_nodes: list[str] | None = None,
) -> MemoryNode:
    """Add a new memory node. Auto-supersedes same-category facts."""
    if node_type not in VALID_NODE_TYPES:
        raise ValueError(f"Invalid node_type: {node_type}. Must be one of {VALID_NODE_TYPES}")

    now = datetime.now(timezone.utc).isoformat()
    node = MemoryNode(
        user_id=user_id,
        node_type=node_type,
        content=content,
        confidence=confidence,
        source_session=source_session,
        created_at=now,
        updated_at=now,
        related_nodes=related_nodes or [],
    )

    # Auto-supersede same-category facts
    if node_type == "fact":
        category = _detect_category(content)
        if category:
            existing = get_nodes(user_id, node_type="fact", active_only=True)
            for old_node in existing:
                if _detect_category(old_node.content) == category:
                    supersede_node(old_node.id)
                    # Create a contradiction if the old content differs significantly
                    if old_node.content.lower().strip() != content.lower().strip():
                        _maybe_add_contradiction(user_id, old_node, node, source_session)

    # Check for duplicate content
    existing = get_nodes(user_id, node_type=node_type, active_only=True)
    for e in existing:
        if e.content.lower().strip() == content.lower().strip():
            # Update confidence instead of duplicating
            _update_confidence(e.id, max(e.confidence, confidence))
            return e

    db = get_db()
    db.execute(
        "INSERT INTO memory_graph "
        "(id, user_id, node_type, content, confidence, source_session, created_at, updated_at, superseded_at, related_nodes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (node.id, user_id, node_type, encrypt_db(content), confidence,
         source_session, now, now, None, json.dumps(node.related_nodes)),
    )
    db.commit()
    return node


def get_nodes(
    user_id: str,
    node_type: str | None = None,
    active_only: bool = True,
    limit: int = 50,
) -> list[MemoryNode]:
    """Query memory nodes by type and active status."""
    db = get_db()
    conditions = ["user_id = ?"]
    params: list = [user_id]

    if node_type:
        conditions.append("node_type = ?")
        params.append(node_type)
    if active_only:
        conditions.append("superseded_at IS NULL")

    where = " AND ".join(conditions)
    rows = db.execute(
        f"SELECT * FROM memory_graph WHERE {where} ORDER BY created_at DESC LIMIT ?",
        params + [limit],
    ).fetchall()

    nodes = []
    for row in rows:
        try:
            content = decrypt_db(row["content"])
        except Exception:
            content = row["content"]  # fallback if not encrypted
        nodes.append(MemoryNode(
            id=row["id"],
            user_id=row["user_id"],
            node_type=row["node_type"],
            content=content,
            confidence=row["confidence"],
            source_session=row["source_session"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            superseded_at=row["superseded_at"],
            related_nodes=json.loads(row["related_nodes"]) if row["related_nodes"] else [],
        ))
    return nodes


def get_open_threads(user_id: str) -> list[MemoryNode]:
    """Get all active thread-type nodes — unresolved topics worth following up on."""
    return get_nodes(user_id, node_type="thread", active_only=True)


def get_commitments(user_id: str) -> list[MemoryNode]:
    """Get all active commitments — things that were promised or planned."""
    return get_nodes(user_id, node_type="commitment", active_only=True)


def find_contradictions(user_id: str) -> list[MemoryNode]:
    """Get all active contradiction nodes."""
    return get_nodes(user_id, node_type="contradiction", active_only=True)


def supersede_node(node_id: str) -> None:
    """Mark a node as superseded (retired)."""
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "UPDATE memory_graph SET superseded_at = ?, updated_at = ? WHERE id = ?",
        (now, now, node_id),
    )
    db.commit()


def delete_node(node_id: str, user_id: str) -> bool:
    """Delete a node — user-facing deletion. Returns True if deleted."""
    db = get_db()
    result = db.execute(
        "DELETE FROM memory_graph WHERE id = ? AND user_id = ?",
        (node_id, user_id),
    )
    db.commit()
    return result.rowcount > 0


def delete_nodes_by_date_range(
    user_id: str,
    start_date: str,
    end_date: str,
    node_types: list[str] | None = None,
) -> int:
    """Bulk delete by date range — used for emotional node deletion."""
    db = get_db()
    conditions = ["user_id = ?", "created_at >= ?", "created_at <= ?"]
    params: list = [user_id, start_date, end_date]
    if node_types:
        placeholders = ",".join("?" * len(node_types))
        conditions.append(f"node_type IN ({placeholders})")
        params.extend(node_types)

    where = " AND ".join(conditions)
    result = db.execute(f"DELETE FROM memory_graph WHERE {where}", params)
    db.commit()
    return result.rowcount


def get_nodes_for_prompt(user_id: str) -> dict[str, list[str]]:
    """Get active nodes grouped by type for prompt injection."""
    nodes = get_nodes(user_id, active_only=True, limit=30)
    grouped: dict[str, list[str]] = {}
    for node in nodes:
        if node.node_type not in grouped:
            grouped[node.node_type] = []
        grouped[node.node_type].append(node.content)
    return grouped


# ── Internal helpers ──────────────────────────────────────────────────────────

def _update_confidence(node_id: str, confidence: float) -> None:
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "UPDATE memory_graph SET confidence = ?, updated_at = ? WHERE id = ?",
        (confidence, now, node_id),
    )
    db.commit()


def _maybe_add_contradiction(
    user_id: str,
    old_node: MemoryNode,
    new_node: MemoryNode,
    source_session: str,
) -> None:
    """Create a contradiction node linking two conflicting facts."""
    content = f"Conflict: previously '{old_node.content}', now '{new_node.content}'"
    add_node(
        user_id=user_id,
        node_type="contradiction",
        content=content,
        confidence=0.8,
        source_session=source_session,
        related_nodes=[old_node.id, new_node.id],
    )
