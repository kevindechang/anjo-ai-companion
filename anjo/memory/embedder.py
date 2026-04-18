"""Sentence-transformer wrapper for dual embeddings (semantic + emotional)."""

from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer

_SEMANTIC_MODEL = "all-MiniLM-L6-v2"

# Emotional phrasing prefix nudges the model toward affective dimensions
_EMOTIONAL_PREFIX = "This is how this conversation felt emotionally: "


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    try:
        return SentenceTransformer(_SEMANTIC_MODEL)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load SentenceTransformer model '{_SEMANTIC_MODEL}': {exc}"
        ) from exc


def embed_semantic(text: str) -> list[float]:
    """Embed text for semantic/topical similarity."""
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    return _get_model().encode(text, normalize_embeddings=True).tolist()


def embed_emotional(text: str) -> list[float]:
    """Embed text with an emotional framing prefix."""
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    return _get_model().encode(_EMOTIONAL_PREFIX + text, normalize_embeddings=True).tolist()
