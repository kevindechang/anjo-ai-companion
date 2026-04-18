"""User input classifier for OCC emotional appraisal.

Rules handle structural signals (ABUSE, CASUAL, NEGLECT) where consistency matters
and keyword precision is high. VADER handles valence and nuanced intent — it understands
negation ("not happy") and basic sarcasm ("oh great") that keyword lists miss.

No API calls — runs synchronously in <5ms before every LLM request.
"""

from __future__ import annotations

import logging
import re
import threading

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

logger = logging.getLogger(__name__)

_vader: SentimentIntensityAnalyzer | None = None
_vader_lock = threading.Lock()


def _get_vader() -> SentimentIntensityAnalyzer:
    global _vader
    if _vader is None:
        with _vader_lock:
            if _vader is None:
                _vader = SentimentIntensityAnalyzer()
    return _vader


# ── Keyword sets (structural signals only) ────────────────────────────────────

_AGGRESSIVE_WORDS = {
    "wtf",
    "stupid",
    "idiot",
    "dumb",
    "shut up",
    "shut the",
    "useless",
    "worthless",
    "pathetic",
    "garbage",
    "trash",
    "terrible",
    "awful",
    "hate you",
    "hate this",
    "worst",
    "disgusting",
    "horrible",
    "moron",
    "loser",
    "shut it",
}

_VULNERABLE_WORDS = {
    "struggle",
    "struggling",
    "sad",
    "depressed",
    "depression",
    "anxiety",
    "anxious",
    "lonely",
    "alone",
    "scared",
    "afraid",
    "fear",
    "crying",
    "cry",
    "hurt",
    "pain",
    "broken",
    "lost",
    "hopeless",
    "overwhelmed",
    "exhausted",
}

# Phrases that can't be caught by word-set intersection
_VULNERABLE_PHRASES = [
    "tired of",
    "can't cope",
    "falling apart",
    "not okay",
    "bad day",
    "hard time",
    "difficult time",
    "going through",
    "don't know how",
    "can't handle",
    "can't do this",
    "feel empty",
    "feel numb",
]

_CASUAL_PATTERNS = {
    "ok",
    "okay",
    "k",
    "sure",
    "yeah",
    "yep",
    "yes",
    "no",
    "cool",
    "nice",
    "good",
    "right",
    "true",
    "fine",
}

_NEGLECT_PATTERNS = {
    "meh",
    "idk",
    "whatever",
    "lol",
    "haha",
    "hmm",
    "uh",
    "eh",
    "nope",
    "nah",
}

_CHALLENGE_SIGNALS = {
    "actually",
    "disagree",
    "wrong",
    "incorrect",
    "not true",
    "no but",
    "i don't think",
    "i doubt",
    "prove it",
    "are you sure",
    "really though",
    "but wait",
    "but actually",
    "that's not",
    "you're wrong",
    "not sure i agree",
    "i'm not sure about that",
    "i don't agree",
    "i disagree",
    "don't think so",
    "i would push back",
}

_COMMAND_STARTS = {
    "do this",
    "do it",
    "just do",
    "tell me",
    "give me",
    "make it",
    "you must",
    "you have to",
    "you need to",
    "answer me",
    "answer this",
    "stop being",
    "be more",
    "don't do",
    "don't say",
    "never say",
    "always do",
    "simply",
    "just answer",
    "just say",
    "quickly",
}

_APOLOGY_WORDS = {
    "sorry",
    "apologize",
    "apologies",
    "my bad",
    "i was wrong",
    "forgive me",
    "i apologize",
    "i'm sorry",
    "im sorry",
    "my fault",
    "i shouldn't have",
    "that was wrong",
    "i regret",
}

_DEEP_THEORY_WORDS = {
    "theory",
    "hypothesis",
    "philosophy",
    "philosophical",
    "consciousness",
    "metaphysics",
    "ontology",
    "epistemology",
    "paradox",
    "ethics",
    "determinism",
    "existential",
    "cognitive",
    "framework",
    "paradigm",
    "dialectic",
    "phenomenology",
    "subjective",
    "objective",
    "emergent",
    "complexity",
    "systems thinking",
    "first principles",
    "abstract",
    "conceptual",
    "underlying",
    "fundamentally",
    "mechanism",
    "causality",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_vulnerable(lower: str, words: set[str]) -> bool:
    if words & _VULNERABLE_WORDS:
        return True
    return any(p in lower for p in _VULNERABLE_PHRASES)


def _is_abuse(text: str, lower: str) -> bool:
    """Rule-based — structural signals that should always map to ABUSE."""
    alpha = re.sub(r"[^a-zA-Z\s]", "", text)
    caps_ratio = sum(1 for c in alpha if c.isupper()) / max(len(alpha), 1)
    if caps_ratio > 0.6 and len(alpha) > 20:
        return True
    for phrase in _AGGRESSIVE_WORDS:
        if phrase in lower:
            return True
    if text.count("!") >= 3 or text.count("?!") >= 2:
        return True
    return False


def _vader_valence(text: str) -> float:
    """VADER compound score normalised to [0.0, 1.0].

    Handles negation ("not happy" → low), sarcasm ("oh great" in negative context → low),
    and mixed sentiment — all things keyword lists miss.
    """
    compound = _get_vader().polarity_scores(text)["compound"]  # -1.0 to 1.0
    return round((compound + 1.0) / 2.0, 3)  # → 0.0 to 1.0


# ── Public classifiers ────────────────────────────────────────────────────────


def classify_input(text: str) -> tuple[str, float]:
    """Classify user message and return (input_type, valence).

    DEPRECATED: uses a legacy vocabulary ('aggressive', 'command', 'vulnerable',
    'helpful', 'deep_theory', 'neutral') that diverges from the canonical intent
    vocabulary used by classify_intent() and appraise_input(). Use classify_intent()
    for all new code.

    input_type: one of 'aggressive', 'command', 'vulnerable',
                        'helpful', 'deep_theory', 'neutral'
    valence:    float [0.0–1.0] — VADER-derived, handles negation and sarcasm
    """
    lower = text.lower().strip()
    words = set(re.findall(r"\b\w+\b", lower))
    valence = _vader_valence(text)

    if _is_abuse(text, lower):
        return "aggressive", min(valence, 0.2)

    if _is_vulnerable(lower, words):
        return "vulnerable", min(valence, 0.5)

    # Short messages (≤3 words) are casual/neutral regardless of VADER score —
    # VADER over-scores isolated words like "yeah", "ok", "nice"
    word_list = re.findall(r"\b\w+\b", lower)
    if len(word_list) <= 3:
        return "neutral", valence

    # VADER low score catches sarcasm/negativity the warm-word list would miss
    if valence >= 0.7:
        return "helpful", valence

    for phrase in _COMMAND_STARTS:
        if lower.startswith(phrase) or f" {phrase}" in lower:
            if valence < 0.7:
                return "command", valence

    theory_hits = words & _DEEP_THEORY_WORDS
    if (len(theory_hits) >= 2) or (len(theory_hits) >= 1 and len(text) > 120):
        return "deep_theory", valence

    return "neutral", valence


_INTENT_SYSTEM = (
    "Classify this message into exactly one intent. Return only the category name, nothing else.\n\n"
    "ABUSE — hostile, insulting, aggressive, demeaning\n"
    "APOLOGY — saying sorry, admitting fault, asking forgiveness\n"
    "VULNERABILITY — sharing pain, fear, loneliness, struggle, feeling lost or overwhelmed\n"
    "CURIOSITY — genuine questions, intellectual engagement, exploring ideas, deep topics\n"
    "CHALLENGE — disagreeing, pushing back, questioning, doubting something said\n"
    "NEGLECT — disengaged, dismissive deflection (meh, idk, whatever, lol used as avoidance)\n"
    "CASUAL — light, conversational, no strong emotional charge, small talk, just being present"
)

_VALID_INTENTS = {
    "ABUSE",
    "APOLOGY",
    "VULNERABILITY",
    "CURIOSITY",
    "CHALLENGE",
    "NEGLECT",
    "CASUAL",
}


def classify_intent_llm(
    text: str, user_id: str | None = None, session_id: str | None = None
) -> str:
    """Classify user intent via Haiku. Falls back to rule-based on any failure.

    Keeps the hardcoded ABUSE pre-filter (fast, safety-critical).
    Everything else goes to Haiku for accurate nuanced classification.
    """
    lower = text.lower().strip()
    # Fast ABUSE gate — no LLM needed for structural safety signals
    if _is_abuse(text, lower):
        return "ABUSE"

    from anjo.core.llm import MODEL_BACKGROUND, get_client

    try:
        resp = get_client().messages.create(
            model=MODEL_BACKGROUND,
            max_tokens=10,
            system=[
                {"type": "text", "text": _INTENT_SYSTEM, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": text}],
            extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
        )
        if not resp.content or not hasattr(resp.content[0], "text"):
            logger.warning(
                "classify_intent_llm: unexpected LLM response, falling back (user_id=%s session_id=%s)",
                user_id,
                session_id,
            )
            return classify_intent(text)
        result = resp.content[0].text.strip().upper()  # type: ignore[union-attr]
        return result if result in _VALID_INTENTS else classify_intent(text)
    except Exception as exc:
        logger.warning(
            "classify_intent_llm failed, falling back to rule-based (user_id=%s session_id=%s): %s",
            user_id,
            session_id,
            exc,
            exc_info=True,
        )
        return classify_intent(text)


def classify_intent(text: str) -> str:
    """Classify user message into one of 6 OCC intents.

    Returns: ABUSE | APOLOGY | VULNERABILITY | CURIOSITY | CHALLENGE | NEGLECT | CASUAL
    """
    lower = text.lower().strip()
    words = set(re.findall(r"\b\w+\b", lower))
    valence = _vader_valence(text)

    # ABUSE: structural signals — rules own this
    if _is_abuse(text, lower):
        return "ABUSE"

    # APOLOGY: before vulnerability ("sorry I was struggling" → APOLOGY)
    if any(w in lower for w in _APOLOGY_WORDS):
        return "APOLOGY"

    # CASUAL / NEGLECT: short messages — check before VADER vulnerability
    # so "nah" or "meh" don't get pulled into VULNERABILITY by low valence
    word_list = re.findall(r"\b\w+\b", lower)
    if len(word_list) <= 3:
        if words & _NEGLECT_PATTERNS:
            return "NEGLECT"
        if words & _CASUAL_PATTERNS:
            return "CASUAL"

    # CHALLENGE: check before VADER vulnerability so pushback phrases
    # ("not sure I agree") don't score as VULNERABILITY from low valence
    for phrase in _CHALLENGE_SIGNALS:
        if phrase in lower:
            return "CHALLENGE"
    for phrase in _COMMAND_STARTS:
        if lower.startswith(phrase) or f" {phrase}" in lower:
            if valence < 0.65:
                return "CHALLENGE"

    # VULNERABILITY: keyword/phrase match OR VADER strongly negative
    if _is_vulnerable(lower, words) or valence < 0.3:
        return "VULNERABILITY"

    # CURIOSITY: intellectual, warm (VADER-confirmed), or genuine questions
    if words & _DEEP_THEORY_WORDS:
        return "CURIOSITY"
    if valence >= 0.65:
        return "CURIOSITY"
    if text.count("?") >= 2:
        return "CURIOSITY"

    return "CASUAL"
