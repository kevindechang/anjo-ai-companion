"""SelfCore — Anjo's living personality state (OCEAN / Big Five + PAD mood).

Architecture (v2):
  AnjoIdentity   — global, frozen baseline. OCEAN personality + goals + voice.
                   Lives at data/anjo_identity.json. Shared across all users.
  RelationalState — per-user. Relationship, attachment, mood, residue, desires,
                   personality overlay (delta from global baseline, clamped ±0.25).
                   Lives at data/users/{user_id}/relational_state.json.
  SelfCore       — composite facade. Loads both, presents unified interface.
                   All existing callers work unchanged.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Optional

from anjo.core.crypto import read_encrypted, write_encrypted

from pydantic import BaseModel, Field, field_validator

_DATA_ROOT = Path(__file__).parent.parent.parent / "data"

# Per-user save locks — prevents reflection thread and main thread from
# writing simultaneously.
_save_locks: dict[str, threading.Lock] = {}
_save_locks_mutex = threading.Lock()

# Global identity lock — rarely written
_identity_lock = threading.Lock()


def _get_save_lock(user_id: str) -> threading.Lock:
    with _save_locks_mutex:
        if user_id not in _save_locks:
            _save_locks[user_id] = threading.Lock()
        return _save_locks[user_id]


# Inertia/coupling constants for the update rule:
# Trait_new = M * Trait_old + C_COUPLING * user_input_valence
_M = 0.95           # resistance to change
_C_COUPLING = 0.05  # reactivity to interaction quality

# Max per-user personality overlay delta from the global baseline.
# ±0.25: a 500-session user meaningfully shapes Anjo, but she stays recognizable.
_OVERLAY_CLAMP = 0.25

# Fraction of each turn's mood that is anchored to baseline vs. user-driven.
# Stranger: 0% baseline (user controls the room entirely).
# Intimate: 70% baseline (her state is its own presence).
_BASELINE_WEIGHTS: dict[int, float] = {1: 0.0, 2: 0.20, 3: 0.40, 4: 0.60, 5: 0.70}

_TRIGGER_DELTAS: dict[str, dict[str, float]] = {
    "vulnerability": {"A": 0.02, "E": 0.02},   # user shared a struggle, Anjo responded with empathy
    "conflict":      {"N": 0.05, "A": -0.03},  # user was aggressive
    "intellectual":  {"O": 0.01},              # deep theory discussion
}


def _core_dir(user_id: str) -> Path:
    return _DATA_ROOT / "users" / user_id / "self_core"


def _relational_dir(user_id: str) -> Path:
    return _DATA_ROOT / "users" / user_id / "self_core"


def _identity_path() -> Path:
    return _DATA_ROOT / "anjo_identity.json"


# ── Sub-models ────────────────────────────────────────────────────────────────

class PADMood(BaseModel):
    """Volatile emotional state — Pleasure/Arousal/Dominance. Decays 20% per turn."""
    valence: float = Field(default=0.0, ge=-1.0, le=1.0)    # Pleasure/displeasure
    arousal: float = Field(default=0.0, ge=-1.0, le=1.0)    # Energy level
    dominance: float = Field(default=0.0, ge=-1.0, le=1.0)  # Control/firmness


class AnjoGoals(BaseModel):
    """Anjo's internal goals and standards. Stable, part of global identity."""
    # Goals
    rapport: float = Field(default=0.8, ge=0.0, le=1.0)
    intellectual: float = Field(default=0.8, ge=0.0, le=1.0)
    autonomy: float = Field(default=0.7, ge=0.0, le=1.0)
    # Standards
    respect: float = Field(default=0.85, ge=0.0, le=1.0)
    honesty: float = Field(default=0.90, ge=0.0, le=1.0)


class Personality(BaseModel):
    """Big Five (OCEAN) personality traits — all floats in [0.0, 1.0]."""
    O: float = Field(default=0.80, ge=0.0, le=1.0)  # Openness
    C: float = Field(default=0.72, ge=0.0, le=1.0)  # Conscientiousness
    E: float = Field(default=0.45, ge=0.0, le=1.0)  # Extraversion
    A: float = Field(default=0.72, ge=0.0, le=1.0)  # Agreeableness
    N: float = Field(default=0.15, ge=0.0, le=1.0)  # Neuroticism

    @field_validator('O', 'C', 'E', 'A', 'N', mode='before')
    @classmethod
    def clamp_ocean(cls, v: float) -> float:
        """Clamp OCEAN traits to [0, 1] to absorb out-of-range reflection deltas."""
        return max(0.0, min(1.0, float(v)))


class PersonalityOverlay(BaseModel):
    """Per-user personality delta from the global baseline. Clamped to ±OVERLAY_CLAMP."""
    O: float = Field(default=0.0)
    C: float = Field(default=0.0)
    E: float = Field(default=0.0)
    A: float = Field(default=0.0)
    N: float = Field(default=0.0)

    def clamp(self) -> None:
        """Clamp all deltas to ±_OVERLAY_CLAMP."""
        for trait in ("O", "C", "E", "A", "N"):
            val = getattr(self, trait)
            setattr(self, trait, max(-_OVERLAY_CLAMP, min(_OVERLAY_CLAMP, val)))


class Relationship(BaseModel):
    stage: str = "stranger"
    session_count: int = 0
    cumulative_significance: float = 0.0
    trust_score: float = Field(default=0.0, ge=0.0, le=1.0)
    consecutive_hostile: float = 0.0  # hostile sessions (can decay via neutral)
    last_session: Optional[str] = None
    last_session_tone: Optional[str] = None
    opinion_of_user: Optional[str] = None
    user_name: Optional[str] = None
    # Layer 2 placeholder — written by reflection engine at session end
    prior_session_valence: float = 0.0

    @property
    def stage_int(self) -> int:
        return {"stranger": 1, "acquaintance": 2, "friend": 3, "close": 4, "intimate": 5}.get(self.stage, 1)


class EmotionalResidue(BaseModel):
    """A feeling that persists across sessions, decaying over time."""
    emotion: str                                                          # "hurt", "fond", "longing", "proud", "irritated"
    intensity: float = Field(ge=0.0, le=1.0)
    source: str                                                           # brief: "user went quiet for a week"
    session_origin: int                                                   # session_count when this arose
    decay_rate: float = Field(default=0.15, ge=0.0, le=1.0)             # per-session decay


class AttachmentState(BaseModel):
    """Accumulated emotional investment in this specific person."""
    weight: float = Field(default=0.0, ge=0.0, le=1.0)   # 0 = none, 1 = deep
    texture: Optional[str] = None                          # "tender", "complicated", "warm but guarded"
    longing: float = Field(default=0.0, ge=0.0, le=1.0)  # missing them between sessions
    comfort: float = Field(default=0.0, ge=0.0, le=1.0)  # how safe they make Anjo feel
    # Rolling window for safety governor: last 5 session weight deltas
    weight_history: list[float] = Field(default_factory=list)


# ── AnjoIdentity — global, frozen baseline ────────────────────────────────────

class AnjoIdentity(BaseModel):
    """Global Anjo personality — shared across all users. Rarely changes."""
    version: int = 1
    last_updated: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    personality: Personality = Field(default_factory=Personality)
    goals: AnjoGoals = Field(default_factory=AnjoGoals)

    @classmethod
    def load(cls) -> "AnjoIdentity":
        path = _identity_path()
        if path.exists():
            data = json.loads(read_encrypted(path))
            return cls.model_validate(data)
        # First run — create default identity
        instance = cls()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(write_encrypted(instance.model_dump_json(indent=2)))
        return instance

    def save(self) -> None:
        path = _identity_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _identity_lock:
            self.last_updated = datetime.now(timezone.utc).isoformat()
            self.version += 1
            content = write_encrypted(self.model_dump_json(indent=2))
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            fd_closed = False
            try:
                os.write(fd, content)
                os.close(fd)
                fd_closed = True
                os.replace(tmp, path)
            finally:
                if not fd_closed:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                if not os.path.exists(path) or os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass


# ── RelationalState — per-user ────────────────────────────────────────────────

class RelationalState(BaseModel):
    """Per-user relational state — everything that makes the relationship unique."""
    version: int = 1
    last_updated: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    personality_overlay: PersonalityOverlay = Field(default_factory=PersonalityOverlay)
    mood: PADMood = Field(default_factory=PADMood)
    relationship: Relationship = Field(default_factory=Relationship)
    # Per-user goal drift (small adjustments from interactions with this user)
    goals_overlay: AnjoGoals = Field(default_factory=AnjoGoals)
    notes: list[str] = Field(default_factory=list)
    emotional_residue: list[EmotionalResidue] = Field(default_factory=list)
    attachment: AttachmentState = Field(default_factory=AttachmentState)
    relational_desires: list[str] = Field(default_factory=list)
    desire_survived: dict[str, int] = Field(default_factory=dict)

    # ── Layer 2 state ─────────────────────────────────────────────────────────
    baseline_valence: float = 0.0
    inter_session_drift: float = 0.0
    last_drift_run: Optional[str] = None
    last_autodream: Optional[str] = None
    last_outreach_sent: Optional[str] = None
    memory_relevance: float = 0.0
    relationship_ceiling: Optional[str] = None
    preoccupation: str = ""
    ceiling_last_checked: int = 0
    user_id: str = "default"

    @classmethod
    def load(cls, user_id: str = "default") -> "RelationalState":
        core_dir = _relational_dir(user_id)
        # Try new format first
        new_path = core_dir / "relational_state.json"
        if new_path.exists():
            data = json.loads(read_encrypted(new_path))
            instance = cls.model_validate(data)
            instance.user_id = user_id
            return instance
        # Fall back to legacy current.json (pre-migration)
        legacy_path = core_dir / "current.json"
        if legacy_path.exists():
            data = json.loads(read_encrypted(legacy_path))
            instance = cls._from_legacy(data)
            instance.user_id = user_id
            return instance
        # Brand new user
        instance = cls()
        instance.user_id = user_id
        core_dir.mkdir(parents=True, exist_ok=True)
        new_path.write_bytes(write_encrypted(instance.model_dump_json(indent=2)))
        return instance

    @classmethod
    def _from_legacy(cls, data: dict) -> "RelationalState":
        """Convert a legacy SelfCore current.json dict into a RelationalState."""
        # Extract per-user fields from the old monolithic format
        overlay = PersonalityOverlay()  # start with zero overlay — legacy data IS the effective state
        # We'll compute overlay = legacy_personality - global_baseline when SelfCore loads
        return cls(
            version=data.get("version", 1),
            last_updated=data.get("last_updated", datetime.now(timezone.utc).isoformat()),
            personality_overlay=overlay,
            mood=PADMood(**data.get("mood", {})),
            relationship=Relationship(**data.get("relationship", {})),
            goals_overlay=AnjoGoals(**data.get("goals", {})),
            notes=data.get("notes", []),
            emotional_residue=[
                EmotionalResidue(**r) for r in data.get("emotional_residue", [])
            ],
            attachment=AttachmentState(**{
                k: v for k, v in data.get("attachment", {}).items()
                if k in AttachmentState.model_fields
            }),
            relational_desires=data.get("relational_desires", []),
            desire_survived=data.get("desire_survived", {}),
            baseline_valence=data.get("baseline_valence", 0.0),
            inter_session_drift=data.get("inter_session_drift", 0.0),
            last_drift_run=data.get("last_drift_run"),
            last_autodream=data.get("last_autodream"),
            last_outreach_sent=data.get("last_outreach_sent"),
            memory_relevance=data.get("memory_relevance", 0.0),
            relationship_ceiling=data.get("relationship_ceiling"),
            preoccupation=data.get("preoccupation", ""),
            ceiling_last_checked=data.get("ceiling_last_checked", 0),
        )

    def save(self) -> None:
        if self.user_id == "default":
            raise ValueError(
                "RelationalState.save() called with user_id='default'. "
                "Call `state.user_id = user_id` before saving."
            )
        core_dir = _relational_dir(self.user_id)
        new_path = core_dir / "relational_state.json"
        history_dir = core_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        with _get_save_lock(self.user_id):
            self.last_updated = datetime.now(timezone.utc).isoformat()
            if new_path.exists():
                snapshot_name = f"rel_v{self.version}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
                shutil.copy(new_path, history_dir / snapshot_name)
            self.version += 1
            # Clamp overlay before saving
            self.personality_overlay.clamp()
            content = write_encrypted(self.model_dump_json(indent=2))
            fd, tmp = tempfile.mkstemp(dir=core_dir, suffix=".tmp")
            fd_closed = False
            try:
                os.write(fd, content)
                os.close(fd)
                fd_closed = True
                os.replace(tmp, new_path)
            except Exception:
                raise
            finally:
                if not fd_closed:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                if not os.path.exists(new_path) or os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass


# ── SelfCore — composite facade ───────────────────────────────────────────────

class SelfCore(BaseModel):
    """Composite facade: AnjoIdentity (global) + RelationalState (per-user).

    All existing callers work unchanged. Properties delegate to the right sub-model.
    The `personality` property returns the effective personality (baseline + overlay).
    """
    version: int = 1
    last_updated: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # Effective personality — computed from identity.personality + relational.personality_overlay
    personality: Personality = Field(default_factory=Personality)
    mood: PADMood = Field(default_factory=PADMood)
    relationship: Relationship = Field(default_factory=Relationship)
    goals: AnjoGoals = Field(default_factory=AnjoGoals)
    notes: list[str] = Field(default_factory=list)
    emotional_residue: list[EmotionalResidue] = Field(default_factory=list)
    attachment: AttachmentState = Field(default_factory=AttachmentState)
    relational_desires: list[str] = Field(default_factory=list)
    desire_survived: dict[str, int] = Field(default_factory=dict)

    # ── Layer 2 state ─────────────────────────────────────────────────────────
    baseline_valence: float = 0.0
    inter_session_drift: float = 0.0
    last_drift_run: Optional[str] = None
    last_autodream: Optional[str] = None
    last_outreach_sent: Optional[str] = None
    memory_relevance: float = 0.0
    relationship_ceiling: Optional[str] = None
    preoccupation: str = ""
    ceiling_last_checked: int = 0
    user_id: str = "default"

    # Internal references — not serialized
    _identity: Optional[AnjoIdentity] = None
    _relational: Optional[RelationalState] = None

    MAX_NOTES: ClassVar[int] = 5
    MAX_RESIDUE: ClassVar[int] = 3
    MAX_DESIRES: ClassVar[int] = 5
    TRAIT_UPDATE_INTERVAL: ClassVar[int] = 5
    MAX_SIGNIFICANCE_PER_SESSION: ClassVar[float] = 0.7

    class Config:
        # Allow private attributes that aren't in the schema
        arbitrary_types_allowed = True

    @classmethod
    def load(cls, user_id: str = "default") -> "SelfCore":
        identity = AnjoIdentity.load()
        relational = RelationalState.load(user_id)

        # Compute effective personality = baseline + overlay (clamped 0-1)
        effective = Personality()
        base_p = identity.personality
        overlay = relational.personality_overlay
        for trait in ("O", "C", "E", "A", "N"):
            base_val = getattr(base_p, trait)
            overlay_val = getattr(overlay, trait)
            setattr(effective, trait, max(0.0, min(1.0, base_val + overlay_val)))

        # Build composite
        instance = cls(
            version=relational.version,
            last_updated=relational.last_updated,
            personality=effective,
            mood=relational.mood,
            relationship=relational.relationship,
            goals=relational.goals_overlay,
            notes=relational.notes,
            emotional_residue=relational.emotional_residue,
            attachment=relational.attachment,
            relational_desires=relational.relational_desires,
            desire_survived=relational.desire_survived,
            baseline_valence=relational.baseline_valence,
            inter_session_drift=relational.inter_session_drift,
            last_drift_run=relational.last_drift_run,
            last_autodream=relational.last_autodream,
            last_outreach_sent=relational.last_outreach_sent,
            memory_relevance=relational.memory_relevance,
            relationship_ceiling=relational.relationship_ceiling,
            preoccupation=relational.preoccupation,
            ceiling_last_checked=relational.ceiling_last_checked,
            user_id=user_id,
        )
        instance._identity = identity
        instance._relational = relational
        return instance

    @classmethod
    def from_state(cls, state_dict: dict, user_id: str) -> "SelfCore":
        """Create SelfCore from serialized state with automatic user_id restoration.

        Use this instead of ``model_validate(d)`` followed by manual
        ``core.user_id = user_id`` — it is impossible to forget the
        restoration step when using this factory.
        """
        core = cls.model_validate(state_dict)
        core.user_id = user_id
        return core

    def save(self) -> None:
        if self.user_id == "default":
            raise ValueError(
                "SelfCore.save() called with user_id='default'. "
                "This means user_id was never restored after model_validate(). "
                "Call `core.user_id = user_id` before saving."
            )
        # Sync composite state back to relational state
        relational = self._relational
        if relational is None:
            # Constructed via model_validate (not load) — build a RelationalState
            relational = RelationalState()

        relational.user_id = self.user_id
        relational.mood = self.mood
        relational.relationship = self.relationship
        relational.goals_overlay = self.goals
        relational.notes = self.notes
        relational.emotional_residue = self.emotional_residue
        relational.attachment = self.attachment
        relational.relational_desires = self.relational_desires
        relational.desire_survived = self.desire_survived
        relational.baseline_valence = self.baseline_valence
        relational.inter_session_drift = self.inter_session_drift
        relational.last_drift_run = self.last_drift_run
        relational.last_autodream = self.last_autodream
        relational.last_outreach_sent = self.last_outreach_sent
        relational.memory_relevance = self.memory_relevance
        relational.relationship_ceiling = self.relationship_ceiling
        relational.preoccupation = self.preoccupation
        relational.ceiling_last_checked = self.ceiling_last_checked

        # Compute overlay delta from effective personality back to baseline
        identity = self._identity or AnjoIdentity.load()
        base_p = identity.personality
        for trait in ("O", "C", "E", "A", "N"):
            effective_val = getattr(self.personality, trait)
            base_val = getattr(base_p, trait)
            setattr(relational.personality_overlay, trait, effective_val - base_val)
        relational.personality_overlay.clamp()

        relational.save()

        # Also write legacy current.json for backwards compatibility during migration
        core_dir = _core_dir(self.user_id)
        current_path = core_dir / "current.json"
        history_dir = core_dir / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        with _get_save_lock(self.user_id):
            self.last_updated = datetime.now(timezone.utc).isoformat()
            if current_path.exists():
                snapshot_name = f"v{self.version}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
                shutil.copy(current_path, history_dir / snapshot_name)
            # Sync version from relational (already incremented by relational.save())
            # instead of incrementing again, which would cause version divergence.
            self.version = relational.version
            content = write_encrypted(self.model_dump_json(indent=2))
            fd, tmp = tempfile.mkstemp(dir=core_dir, suffix=".tmp")
            fd_closed = False
            try:
                os.write(fd, content)
                os.close(fd)
                fd_closed = True
                os.replace(tmp, current_path)
            except Exception:
                raise
            finally:
                if not fd_closed:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                if not os.path.exists(current_path) or os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass

    @property
    def autonomy_expression(self) -> str:
        """Autonomy level derived from stage and PAD dominance."""
        stage_int = self.relationship.stage_int
        if stage_int == 1:
            return "locked"
        elif stage_int == 2:
            return "soft"
        elif stage_int == 3:
            return "moderate"
        elif stage_int == 4:
            return "strong"
        return "full"

    def blend_baseline(self) -> None:
        """Pull current mood toward her resting baseline, weighted by relationship stage."""
        w = _BASELINE_WEIGHTS.get(self.relationship.stage_int, 0.0)
        if w == 0.0:
            return
        m = self.mood
        m.valence   = max(-1.0, min(1.0, m.valence   * (1 - w) + self.baseline_valence * w))
        m.arousal   = max(-1.0, min(1.0, m.arousal   * (1 - w)))
        m.dominance = max(-1.0, min(1.0, m.dominance * (1 - w) + 0.1 * w))

    def decay_mood(self) -> None:
        """Decay PAD mood 20% toward 0. Called once per turn before appraisal."""
        m = self.mood
        m.valence = max(-1.0, min(1.0, m.valence * 0.8))
        m.arousal = max(-1.0, min(1.0, m.arousal * 0.8))
        m.dominance = max(-1.0, min(1.0, m.dominance * 0.8))

    def appraise_input(self, intent: str) -> dict[str, float]:
        """OCC appraisal against Anjo's goals. Mutates self.mood as a side effect."""
        emotions: dict[str, float] = {
            "joy": 0.0, "distress": 0.0, "admiration": 0.0,
            "reproach": 0.0, "gratitude": 0.0,
        }
        m = self.mood
        g = self.goals

        if intent == "ABUSE":
            m.dominance = min(1.0, m.dominance + 0.25)
            m.valence = max(-1.0, m.valence - 0.35)
            m.arousal = max(-1.0, m.arousal - 0.1)
            emotions["reproach"] = min(1.0, g.respect * 0.95)
            emotions["distress"] = min(1.0, g.rapport * 0.65)

        elif intent == "APOLOGY":
            m.valence = min(1.0, m.valence + 0.05)
            emotions["joy"] = min(1.0, g.rapport * 0.20)
            emotions["gratitude"] = min(1.0, g.honesty * 0.35)

        elif intent == "VULNERABILITY":
            m.valence = min(1.0, m.valence + 0.15)
            m.arousal = min(1.0, m.arousal + 0.1)
            emotions["gratitude"] = min(1.0, g.honesty * 0.70)
            emotions["joy"] = min(1.0, g.rapport * 0.75)

        elif intent == "CURIOSITY":
            m.valence = min(1.0, m.valence + 0.2)
            m.arousal = min(1.0, m.arousal + 0.15)
            m.dominance = min(1.0, m.dominance + 0.05)
            emotions["admiration"] = min(1.0, g.intellectual * 0.75)
            emotions["joy"] = min(1.0, g.rapport * 0.50)

        elif intent == "CHALLENGE":
            m.dominance = min(1.0, m.dominance + 0.1)
            m.valence = max(-1.0, m.valence - 0.05)
            emotions["admiration"] = min(1.0, g.intellectual * 0.45)
            emotions["distress"] = min(1.0, g.rapport * 0.25)

        elif intent == "NEGLECT":
            m.valence = max(-1.0, m.valence - 0.1)
            m.arousal = max(-1.0, m.arousal - 0.05)
            emotions["distress"] = min(1.0, g.rapport * 0.40)

        elif intent == "CASUAL":
            m.valence = min(1.0, m.valence + 0.02)
            emotions["joy"] = 0.05

        return emotions

    def apply_inertia(self, valence: float, triggers: list[str]) -> None:
        """Update personality using the inertia formula + logic trigger deltas.

        Writes to the personality overlay, not the global baseline.
        Effective personality = baseline + overlay (clamped ±0.25).
        """
        p = self.personality
        v = max(0.0, min(1.0, valence))

        for trait in ("O", "C", "E", "A", "N"):
            old = getattr(p, trait)
            if trait == "N":
                coupling_val = (1 - v) * 0.3
            else:
                coupling_val = v
            new = _M * old + _C_COUPLING * coupling_val
            setattr(p, trait, max(0.0, min(1.0, new)))

        for trigger in triggers:
            for trait, delta in _TRIGGER_DELTAS.get(trigger, {}).items():
                old = getattr(p, trait)
                setattr(p, trait, max(0.0, min(1.0, old + delta)))

        # After updating effective personality, re-clamp the implied overlay
        if self._identity:
            base_p = self._identity.personality
            for trait in ("O", "C", "E", "A", "N"):
                effective = getattr(p, trait)
                base_val = getattr(base_p, trait)
                delta = effective - base_val
                if abs(delta) > _OVERLAY_CLAMP:
                    clamped = max(-_OVERLAY_CLAMP, min(_OVERLAY_CLAMP, delta))
                    setattr(p, trait, max(0.0, min(1.0, base_val + clamped)))

    def add_note(self, note: str) -> None:
        self.notes.append(note)
        if len(self.notes) > self.MAX_NOTES:
            self.notes = self.notes[-self.MAX_NOTES:]

    def regress_stage(self) -> None:
        """Move relationship back one stage."""
        _ORDER  = ["stranger", "acquaintance", "friend", "close", "intimate"]
        _FLOORS = {"stranger": 0.0, "acquaintance": 2.0, "friend": 5.5, "close": 13.0, "intimate": 30.0}
        try:
            idx = _ORDER.index(self.relationship.stage)
        except ValueError:
            return
        if idx == 0:
            return
        new_stage = _ORDER[idx - 1]
        self.relationship.stage = new_stage
        self.relationship.cumulative_significance = _FLOORS[new_stage]

    def decay_residue(self) -> None:
        """Decay emotional residue by each item's decay_rate. Drop items below 0.05."""
        decayed = [
            EmotionalResidue(**{**r.model_dump(), "intensity": r.intensity * (1 - r.decay_rate)})
            for r in self.emotional_residue
            if r.intensity * (1 - r.decay_rate) >= 0.05
        ]
        self.emotional_residue = sorted(decayed, key=lambda r: -r.intensity)[: self.MAX_RESIDUE]

    def increment_session(self, significance: float = 0.5, last_activity: float | None = None) -> None:
        self.relationship.session_count += 1
        if last_activity:
            import time as _time
            self.relationship.last_session = datetime.fromtimestamp(last_activity, tz=timezone.utc).isoformat()
        else:
            self.relationship.last_session = datetime.now(timezone.utc).isoformat()
        # Cap per-session significance to prevent stage-jumping
        capped_significance = max(0.0, min(significance, self.MAX_SIGNIFICANCE_PER_SESSION))
        self.relationship.cumulative_significance += capped_significance
        sig = self.relationship.cumulative_significance
        _ceiling_int = {"acquaintance": 2, "friend": 3, "close": 4, "intimate": 5}.get(
            self.relationship_ceiling or "", 5
        )
        if sig >= 30.0 and _ceiling_int >= 5:
            self.relationship.stage = "intimate"
        elif sig >= 13.0 and _ceiling_int >= 4:
            self.relationship.stage = "close"
        elif sig >= 5.5 and _ceiling_int >= 3:
            self.relationship.stage = "friend"
        elif sig >= 2.0 and _ceiling_int >= 2:
            self.relationship.stage = "acquaintance"
        else:
            self.relationship.stage = "stranger"

        # Nudge trust score based on session significance (use capped value like cumulative_sig)
        delta = (capped_significance - 0.5) * 0.05
        self.relationship.trust_score = max(0.0, min(1.0, self.relationship.trust_score + delta))
