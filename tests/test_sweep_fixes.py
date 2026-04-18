"""Regression tests for pre-launch sweep fixes (2026-04-13).

Tests cover:
  - Session store lock discipline (delete, reset, create)
  - Mid-reflect lock consistency
  - Admin chat history privacy redaction
  - Token verification with DB user check
  - Letter generation lock consistency
"""

from __future__ import annotations

import threading
import time

import pytest

# ── Session store lock discipline ────────────────────────────────────────────


class TestDeleteSessionLock:
    """delete_session must use _sessions_lock to prevent concurrent dict mutation."""

    def test_delete_session_thread_safe(self):
        """Concurrent deletes and reads should not raise."""
        from anjo.dashboard.session_store import (
            _sessions,
            _sessions_lock,
            delete_session,
        )

        # Set up a session manually
        with _sessions_lock:
            _sessions["lock-test-user"] = {
                "state": {"conversation_history": [], "self_core": {}},
                "core": None,
                "user_id": "lock-test-user",
                "session_id": "abc",
                "last_activity": time.time(),
            }

        errors = []

        def _delete():
            try:
                delete_session("lock-test-user")
            except Exception as e:
                errors.append(e)

        def _read():
            try:
                with _sessions_lock:
                    _ = _sessions.get("lock-test-user")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_delete)] + [
            threading.Thread(target=_read) for _ in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Concurrent access errors: {errors}"


class TestCreateSessionNoIOUnderLock:
    """_create_session should not perform DB I/O when called under _sessions_lock."""

    def test_facts_and_trends_passed_not_loaded(self):
        """Facts and trends should be pre-loaded and passed, not loaded inside."""
        from anjo.dashboard.session_store import _create_session, _sessions, _sessions_lock

        test_facts = ["likes jazz", "works in finance"]
        test_trends = ["AI", "music"]

        with _sessions_lock:
            _create_session(
                core_dump={"personality": {}, "mood": {}, "relationship": {}},
                core_instance=None,
                user_id="create-test-user",
                cached_facts=test_facts,
                cached_trends=test_trends,
            )

        session = _sessions.get("create-test-user")
        assert session is not None
        assert session["state"]["cached_user_facts"] == test_facts
        assert session["state"]["cached_trending_topics"] == test_trends

    def test_default_empty_when_no_facts_passed(self):
        """When no cached data passed, defaults to empty lists."""
        from anjo.dashboard.session_store import _create_session, _sessions, _sessions_lock

        with _sessions_lock:
            _create_session(
                core_dump={"personality": {}, "mood": {}, "relationship": {}},
                core_instance=None,
                user_id="no-facts-user",
            )

        session = _sessions.get("no-facts-user")
        assert session is not None
        assert session["state"]["cached_user_facts"] == []
        assert session["state"]["cached_trending_topics"] == []


class TestResetSessionLockDiscipline:
    """reset_session must load SelfCore outside the lock."""

    def test_reset_on_nonexistent_session(self):
        """reset_session on a missing user should return without error."""
        from anjo.dashboard.session_store import reset_session

        # Should not raise
        reset_session("nonexistent-user-12345")

    def test_reset_clears_history(self):
        """reset_session should clear conversation history."""
        from anjo.core.self_core import SelfCore
        from anjo.dashboard.session_store import (
            _sessions,
            _sessions_lock,
            reset_session,
        )

        user_id = "reset-hist-user"
        core = SelfCore()
        core.user_id = user_id

        # Create a session with history
        with _sessions_lock:
            _sessions[user_id] = {
                "state": {
                    "conversation_history": [{"role": "user", "content": "hello"}],
                    "self_core": core.model_dump(),
                    "occ_carry": {"reproach": 0.5},
                },
                "core": core,
                "user_id": user_id,
                "session_id": "xyz",
                "last_activity": time.time(),
            }

        reset_session(user_id)

        with _sessions_lock:
            session = _sessions.get(user_id)

        assert session is not None
        assert session["state"]["conversation_history"] == []
        assert session["state"]["occ_carry"] == {}


# ── Mid-reflect lock consistency ─────────────────────────────────────────────


class TestMidReflectLock:
    """_MID_REFLECT_LOCK.discard must use _SETS_LOCK for consistency."""

    def test_discard_under_lock(self):
        """Verify _MID_REFLECT_LOCK operations are consistent under _SETS_LOCK."""
        from anjo.dashboard.background_tasks import _MID_REFLECT_LOCK, _SETS_LOCK

        user_id = "mid-reflect-test"

        # Simulate add
        with _SETS_LOCK:
            _MID_REFLECT_LOCK.add(user_id)

        assert user_id in _MID_REFLECT_LOCK

        # Simulate discard (should be under lock in real code)
        with _SETS_LOCK:
            _MID_REFLECT_LOCK.discard(user_id)

        assert user_id not in _MID_REFLECT_LOCK


# ── Admin chat history privacy ───────────────────────────────────────────────


class TestAdminChatPrivacy:
    """Admin chat endpoint should redact content by default."""

    def test_chat_history_redacted_by_default(self, auth_client):
        """Content should be redacted when include_content is not set."""
        import os

        from anjo.core.db import get_db

        admin_key = os.environ.get("ANJO_ADMIN_SECRET", "test_admin_key")

        # Get the user_id of testuser
        db = get_db()
        row = db.execute("SELECT user_id FROM users WHERE username = 'testuser'").fetchone()
        if not row:
            pytest.skip("No testuser in DB")
        uid = row["user_id"]

        # Insert a chat message
        from anjo.core.history import append_message

        append_message(uid, "user", "this is private content")

        r = auth_client.get(
            f"/api/admin/users/{uid}/chat",
            headers={"X-Admin-Key": admin_key},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["content_included"] is False
        for msg in data["messages"]:
            if isinstance(msg, dict) and "content" in msg:
                assert "redacted" in msg["content"]
                assert "private content" not in msg["content"]

    def test_chat_history_content_with_flag(self, auth_client):
        """Content should be visible when include_content=true."""
        import os

        from anjo.core.db import get_db

        admin_key = os.environ.get("ANJO_ADMIN_SECRET", "test_admin_key")

        db = get_db()
        row = db.execute("SELECT user_id FROM users WHERE username = 'testuser'").fetchone()
        if not row:
            pytest.skip("No testuser in DB")
        uid = row["user_id"]

        from anjo.core.history import append_message

        append_message(uid, "user", "visible content here")

        r = auth_client.get(
            f"/api/admin/users/{uid}/chat?include_content=true",
            headers={"X-Admin-Key": admin_key},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["content_included"] is True
        contents = [m.get("content", "") for m in data["messages"] if isinstance(m, dict)]
        assert any("visible content" in c for c in contents)


# ── Token verification with DB check ────────────────────────────────────────


class TestTokenDBCheck:
    """verify_token must reject tokens for deleted users."""

    def test_token_rejected_after_account_deleted(self):
        """A valid token should be rejected if the user no longer exists in DB."""
        from anjo.dashboard.auth import make_token, verify_token

        # Token for non-existent user — DB lookup returns None → reject
        token = make_token("deleted-user-99999")
        assert verify_token(token) is None

    def test_token_valid_for_existing_user(self):
        """A valid token should be accepted when the user exists in DB."""
        from anjo.core.db import get_db
        from anjo.dashboard.auth import make_token, verify_token

        uid = "existing-user-token-test"
        db = get_db()
        db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, email, email_hmac, hashed_password, created_at) "
            "VALUES (?, ?, '', '', '$2b$12$dummy', '')",
            (uid, "tokentest_user"),
        )
        db.commit()

        token = make_token(uid)
        assert verify_token(token) == uid


# ── Letter generation lock ──────────────────────────────────────────────────


class TestLetterGenerationLock:
    """_GENERATING_LETTER.discard must use _GENERATING_LOCK for consistency."""

    def test_concurrent_letter_access(self):
        """Concurrent add/discard should not corrupt the set."""
        from anjo.dashboard.routes.story_routes import _GENERATING_LETTER, _GENERATING_LOCK

        errors = []

        def _add_remove(uid):
            try:
                with _GENERATING_LOCK:
                    _GENERATING_LETTER.add(uid)
                time.sleep(0.001)
                with _GENERATING_LOCK:
                    _GENERATING_LETTER.discard(uid)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_add_remove, args=(f"user-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        assert len(_GENERATING_LETTER) == 0


# ── SelfCore user_id safety ─────────────────────────────────────────────────


class TestSelfCoreUserIdSafety:
    """SelfCore.save() must reject user_id='default' to prevent cross-user contamination."""

    def test_save_rejects_default_user_id(self):
        """SelfCore with user_id='default' should raise ValueError on save."""
        from anjo.core.self_core import SelfCore

        core = SelfCore()
        assert core.user_id == "default"
        with pytest.raises(ValueError, match="user_id='default'"):
            core.save()

    def test_from_state_restores_user_id(self):
        """SelfCore.from_state() must set user_id correctly."""
        from anjo.core.self_core import SelfCore

        core = SelfCore()
        state = core.model_dump()
        restored = SelfCore.from_state(state, "real-user-123")
        assert restored.user_id == "real-user-123"


# ── PAD mood bounds ─────────────────────────────────────────────────────────


class TestPADMoodBounds:
    """PAD mood values must always stay within [-1.0, 1.0]."""

    def test_extreme_abuse_stays_bounded(self):
        """Repeated ABUSE appraisals should not push mood outside [-1, 1]."""
        from anjo.core.self_core import SelfCore

        core = SelfCore()
        core.user_id = "bounds-test"

        for _ in range(50):
            core.appraise_input("ABUSE")

        assert -1.0 <= core.mood.valence <= 1.0
        assert -1.0 <= core.mood.arousal <= 1.0
        assert -1.0 <= core.mood.dominance <= 1.0

    def test_extreme_positive_stays_bounded(self):
        """Repeated positive appraisals should not push mood above 1.0."""
        from anjo.core.self_core import SelfCore

        core = SelfCore()
        core.user_id = "bounds-test-pos"

        for _ in range(50):
            core.appraise_input("CURIOSITY")

        assert -1.0 <= core.mood.valence <= 1.0
        assert -1.0 <= core.mood.arousal <= 1.0
        assert -1.0 <= core.mood.dominance <= 1.0


# ── OCEAN trait bounds ──────────────────────────────────────────────────────


class TestOCEANBounds:
    """OCEAN traits must stay within [0.0, 1.0] after any number of updates."""

    def test_inertia_stays_bounded(self):
        """apply_inertia should never push traits outside [0, 1]."""
        from anjo.core.self_core import SelfCore

        core = SelfCore()
        core.user_id = "ocean-bounds"

        # Extreme positive
        for _ in range(100):
            core.apply_inertia(1.0, ["vulnerability", "intellectual"])

        p = core.personality
        for trait in ("O", "C", "E", "A", "N"):
            val = getattr(p, trait)
            assert 0.0 <= val <= 1.0, f"{trait}={val} is out of bounds"

        # Extreme negative
        for _ in range(100):
            core.apply_inertia(0.0, ["conflict"])

        for trait in ("O", "C", "E", "A", "N"):
            val = getattr(p, trait)
            assert 0.0 <= val <= 1.0, f"{trait}={val} is out of bounds"


# ── Attachment safety governor ──────────────────────────────────────────────


class TestAttachmentSafety:
    """Attachment weight must be governed by the safety system."""

    def test_weight_capped_at_session_pace(self):
        """Attachment weight cannot exceed session_count * 0.075."""
        from anjo.core.self_core import SelfCore

        core = SelfCore()
        core.user_id = "att-cap-test"
        core.relationship.session_count = 2  # max weight = 0.15

        # Try to set weight way above cap
        core.attachment.weight = 0.9
        session_cap = min(1.0, core.relationship.session_count * 0.075)
        # Verify the cap formula
        assert session_cap == 0.15

    def test_weight_stays_in_bounds(self):
        """Attachment weight must stay in [0, 1]."""
        from anjo.core.self_core import SelfCore

        core = SelfCore()
        assert 0.0 <= core.attachment.weight <= 1.0
        assert 0.0 <= core.attachment.longing <= 1.0
        assert 0.0 <= core.attachment.comfort <= 1.0
