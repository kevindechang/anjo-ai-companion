"""Rule-based classifier: decides whether long-term memory retrieval is warranted."""
from __future__ import annotations

import re

# Phrases that suggest the user is referencing something from the past
_PAST_REFERENCE_PATTERNS = [
    r"\byou (said|told|mentioned|remember(d)?|asked)\b",
    r"\bthat time\b",
    r"\blast (time|week|month|session|conversation)\b",
    r"\bremember when\b",
    r"\bwe (talked|spoke|discussed)\b",
    r"\byou (once|used to)\b",
    r"\blike (i|you) said\b",
    # Identity / memory check questions
    r"\bwho am i\b",
    r"\bdo you (know|remember) me\b",
    r"\bcheck your memory\b",
    r"\bwhat do you (know|remember) about me\b",
    r"\byou (know|remember) (who|me)\b",
]

# Emotional vocabulary that often warrants memory context
_EMOTIONAL_MARKERS = {
    "lonely", "scared", "afraid", "anxious", "proud", "miss", "hurt",
    "lost", "confused", "overwhelmed", "excited", "devastated", "ashamed",
    "grateful", "hopeful", "frustrated", "empty", "numb", "jealous",
    "betrayed", "vulnerable", "broken", "happy", "sad", "depressed",
}

_PAST_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in _PAST_REFERENCE_PATTERNS]


def should_retrieve(message: str) -> bool:
    """Return True if the message warrants a long-term memory lookup."""
    words = message.lower().split()

    # Very short messages with no emotional content — skip retrieval
    if len(words) < 6 and not any(w in _EMOTIONAL_MARKERS for w in words):
        return False

    # Past-reference phrases strongly suggest retrieval is useful
    for pattern in _PAST_PATTERNS_COMPILED:
        if pattern.search(message):
            return True

    # Emotional vocabulary suggests deep context may be relevant
    if any(w in _EMOTIONAL_MARKERS for w in words):
        return True

    return False
