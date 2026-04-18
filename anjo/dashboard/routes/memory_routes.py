"""Memory API routes — reads ChromaDB collections and reflection log."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from anjo.dashboard.auth import get_current_user_id

router = APIRouter()


@router.get("/reflection-log")
def get_reflection_log(user_id: str = Depends(get_current_user_id)):
    from anjo.reflection.log import read_log

    return {"entries": read_log(user_id, limit=50)}


@router.get("/memories")
def get_memories(user_id: str = Depends(get_current_user_id)):
    from anjo.core.crypto import decrypt_chroma
    from anjo.memory.long_term import _get_collections

    semantic_col, emotional_col = _get_collections()

    def _collection_to_list(col) -> list[dict]:
        result = col.get(where={"user_id": user_id}, include=["documents", "metadatas"])
        items = []
        for mem_id, doc, meta in zip(
            result.get("ids", []),
            result.get("documents", []),
            result.get("metadatas", []),
        ):
            items.append({"id": mem_id, "document": decrypt_chroma(doc), "metadata": meta or {}})
        return items

    return {
        "semantic": _collection_to_list(semantic_col),
        "emotional": _collection_to_list(emotional_col),
    }
