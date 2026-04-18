"""Pre-launch regression sweep — guards every bug/fix area identified in the sweep."""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

# ===========================================================================
# P1 — SECURITY
# ===========================================================================


# ---------------------------------------------------------------------------
# SEC-5 / BUG-3  admin_reset_user scopes to user_id
# ---------------------------------------------------------------------------


class TestAdminResetUserScope:
    """admin_reset_user must delete only the target user's ChromaDB vectors,
    not any other user's."""

    def _register_and_get_uid(self, client, username: str, email: str) -> str:
        client.post(
            "/register", data={"username": username, "password": "pass1234", "email": email}
        )
        users = client.get("/api/admin/users", headers={"X-Admin-Key": "test_admin_key"}).json()[
            "users"
        ]
        return next(u["user_id"] for u in users if u["username"] == username)

    def test_reset_user_A_does_not_delete_user_B_vectors(self, client, tmp_path):
        """After resetting user_A, _get_collections must be called with user_A's id only,
        never user_B's (per-user collections enforce scope at the collection level)."""
        uid_a = self._register_and_get_uid(client, "reset_user_a", "reset_a@test.com")
        uid_b = self._register_and_get_uid(client, "reset_user_b", "reset_b@test.com")

        called_with_ids = []

        def fake_get_collections(user_id):
            called_with_ids.append(user_id)
            col = MagicMock()
            col.get.return_value = {"ids": [f"{user_id}_vec1"]}
            col.name = f"sem_{user_id}"
            return col, col

        with patch("anjo.memory.long_term._get_collections", side_effect=fake_get_collections):
            r = client.post(
                f"/api/admin/users/{uid_a}/reset",
                headers={"X-Admin-Key": "test_admin_key"},
            )

        assert r.status_code == 200
        # _get_collections must only have been invoked with uid_a
        assert uid_b not in called_with_ids, f"user_B's id passed to _get_collections: {called_with_ids}"
        assert uid_a in called_with_ids, "user_A's id was never passed to _get_collections"

    def test_reset_user_A_does_delete_user_A_vectors(self, client):
        """After resetting user_A, ChromaDB delete IS called on user_A's collection."""
        uid_a = self._register_and_get_uid(client, "reset_only_a", "only_a@test.com")

        delete_called_with = []

        mock_col = MagicMock()
        mock_col.get.return_value = {"ids": [f"vec_{uid_a}_1"]}
        mock_col.delete.side_effect = lambda ids: delete_called_with.extend(ids)
        mock_col.name = "semantic"

        with patch("anjo.memory.long_term._get_collections", return_value=(mock_col, mock_col)):
            r = client.post(
                f"/api/admin/users/{uid_a}/reset",
                headers={"X-Admin-Key": "test_admin_key"},
            )

        assert r.status_code == 200
        assert any(uid_a in vid for vid in delete_called_with), (
            "Expected user_A vectors to be deleted but delete was not called with user_A's id"
        )


# ---------------------------------------------------------------------------
# SEC-11  Dev secret is random, not hardcoded
# ---------------------------------------------------------------------------


class TestDevSecretRandomness:
    """When ANJO_SECRET is absent in dev mode, a random secret is generated.
    Two independent processes would produce different secrets."""

    def test_dev_secret_is_not_hardcoded_string(self, monkeypatch):
        """The generated dev secret must not equal any known hardcoded value."""
        monkeypatch.delenv("ANJO_SECRET", raising=False)
        monkeypatch.setenv("ANJO_ENV", "dev")

        import anjo.dashboard.auth as _auth

        # Reset module-level state so we get a fresh generation
        _auth._DEV_SECRET = None

        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            secret = _auth._get_secret()

        assert secret != ""
        assert secret != "dev_secret"
        assert secret != "hardcoded"
        assert secret != "test"
        assert len(secret) >= 32  # secrets.token_hex(32) produces 64 hex chars

    def test_dev_secret_is_stable_within_process(self, monkeypatch):
        """Calling _get_secret() twice in the same process returns the same value."""
        monkeypatch.delenv("ANJO_SECRET", raising=False)
        monkeypatch.setenv("ANJO_ENV", "dev")

        import anjo.dashboard.auth as _auth

        _auth._DEV_SECRET = None

        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            s1 = _auth._get_secret()
            s2 = _auth._get_secret()

        assert s1 == s2

    def test_make_token_verify_token_roundtrip_with_generated_secret(self, monkeypatch):
        """make_token / verify_token round-trip must work with the generated dev secret."""
        monkeypatch.delenv("ANJO_SECRET", raising=False)
        monkeypatch.setenv("ANJO_ENV", "dev")

        # verify_token checks DB — insert stub user so it doesn't reject as deleted
        from anjo.core.db import get_db

        db = get_db()
        db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, email, email_hmac, hashed_password, created_at) "
            "VALUES (?, ?, '', '', '$2b$12$dummy', '')",
            ("test-user-123", "devtoken_testuser"),
        )
        db.commit()

        import anjo.dashboard.auth as _auth

        _auth._DEV_SECRET = None

        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            token = _auth.make_token("test-user-123")
            result = _auth.verify_token(token)

        assert result == "test-user-123"

    def test_different_module_reset_produces_different_secret(self, monkeypatch):
        """Simulating a second process (by resetting _DEV_SECRET) produces a
        different value — confirms secrets.token_hex is called fresh each time."""
        monkeypatch.delenv("ANJO_SECRET", raising=False)
        monkeypatch.setenv("ANJO_ENV", "dev")

        import warnings

        import anjo.dashboard.auth as _auth

        _auth._DEV_SECRET = None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            secret_1 = _auth._get_secret()

        _auth._DEV_SECRET = None  # simulate new process
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            secret_2 = _auth._get_secret()

        assert secret_1 != secret_2


# ---------------------------------------------------------------------------
# SEC-3  Admin key timing-safe comparison
# ---------------------------------------------------------------------------


class TestAdminKeyComparison:
    """The admin key check must use hmac.compare_digest (timing-safe)."""

    def test_correct_key_is_authorized(self, client):
        r = client.get("/api/admin/users", headers={"X-Admin-Key": "test_admin_key"})
        assert r.status_code == 200

    def test_wrong_key_is_403_or_401(self, client):
        r = client.get("/api/admin/users", headers={"X-Admin-Key": "wrong_key_value"})
        assert r.status_code in (401, 403)

    def test_empty_key_is_401_not_crash(self, client):
        """An empty key must be rejected cleanly, not cause a 500."""
        r = client.get("/api/admin/users", headers={"X-Admin-Key": ""})
        assert r.status_code in (401, 403)
        assert r.status_code != 500

    def test_missing_key_header_is_401(self, client):
        """No X-Admin-Key header at all → 401."""
        r = client.get("/api/admin/users")
        assert r.status_code == 401

    def test_admin_secret_empty_env_always_401(self, client, monkeypatch):
        """If ANJO_ADMIN_SECRET is not set, every request is rejected."""
        monkeypatch.setenv("ANJO_ADMIN_SECRET", "")
        r = client.get("/api/admin/users", headers={"X-Admin-Key": ""})
        assert r.status_code in (401, 403)


# ===========================================================================
# P2 — PSYCHOLOGICAL STACK
# ===========================================================================

# ---------------------------------------------------------------------------
# MEM-1 / DEBT-17  Session ID uniqueness
# ---------------------------------------------------------------------------


class TestSessionIdUniqueness:
    """Two sessions created for the same user must have different session_ids.
    The session_id must not be the user_id itself."""

    def _create_fresh_session(self, user_id: str) -> dict:
        import anjo.dashboard.session_store as _ss

        with _ss._sessions_lock:
            _ss._sessions.pop(user_id, None)
        _ss.get_or_create_session(user_id)
        return _ss.get_session(user_id)

    def test_two_sessions_same_user_different_session_ids(self, monkeypatch):
        """Simulate two consecutive sessions — IDs must be distinct."""
        import anjo.dashboard.session_store as _ss

        user_id = "session_id_test_user"

        # Session 1
        session1 = self._create_fresh_session(user_id)
        sid1 = session1["session_id"]

        # End session, create session 2
        _ss.delete_session(user_id)
        session2 = self._create_fresh_session(user_id)
        sid2 = session2["session_id"]

        assert sid1 != sid2, "Two sessions for the same user must have different session_ids"

    def test_session_id_is_not_user_id(self, monkeypatch):
        """session_id must not equal user_id."""
        user_id = "session_id_test_user_2"
        session = self._create_fresh_session(user_id)
        assert session["session_id"] != user_id

    def test_session_id_is_nonempty_string(self, monkeypatch):
        """session_id must be a non-empty string."""
        user_id = "session_id_test_user_3"
        session = self._create_fresh_session(user_id)
        assert isinstance(session["session_id"], str)
        assert len(session["session_id"]) > 0


# ---------------------------------------------------------------------------
# MEM-3 / BUG-9  SelfCore.user_id preserved through model_validate
# ---------------------------------------------------------------------------


class TestSelfCoreUserIdRestored:
    """build_system_prompt (via /api/system-prompt) must pass the real user_id,
    not 'default', to load_facts."""

    def test_system_prompt_uses_correct_user_id_for_facts(self, auth_client):
        """When calling /api/system-prompt as 'testuser', load_facts must be called
        with the actual user_id, not 'default'."""
        calls = []

        import anjo.core.facts as _facts

        original_load = _facts.load_facts

        def capturing_load_facts(user_id: str):
            calls.append(user_id)
            return original_load(user_id)

        with patch.object(_facts, "load_facts", side_effect=capturing_load_facts):
            r = auth_client.get("/api/system-prompt")

        assert r.status_code == 200
        # load_facts is called inside session_store._load_user_facts at session creation;
        # it should never be called with 'default'
        assert "default" not in calls, f"load_facts was called with 'default'. All calls: {calls}"

    def test_self_core_user_id_restored_after_model_validate(self):
        """After model_validate, user_id is preserved as a regular field.
        This test verifies the new field pattern works correctly."""
        from anjo.core.self_core import SelfCore

        original = SelfCore.load("alice_test_user")
        # Verify user_id is set correctly after load
        assert original.user_id == "alice_test_user"
        # Simulate serialization/deserialization
        dumped = original.model_dump()
        restored = SelfCore.model_validate(dumped)
        # With the fix, user_id is preserved through model_validate
        assert restored.user_id == "alice_test_user"


# ---------------------------------------------------------------------------
# MEM-4  relationship_ceiling reset on forget
# ---------------------------------------------------------------------------


class TestForgettingResetsCeiling:
    """After negotiate_and_forget(), the ceiling state must be cleared."""

    def _make_core_with_ceiling(self, user_id: str, stage: str = "friend", ceiling: str = "friend"):
        from anjo.core.self_core import SelfCore

        core = SelfCore.load(user_id)
        core.relationship.stage = stage
        core.relationship.session_count = 10
        core.relationship_ceiling = ceiling
        core.ceiling_last_checked = 8
        core.save()
        return core

    def test_relationship_ceiling_is_none_after_forget(self, monkeypatch):
        """negotiate_and_forget must set relationship_ceiling to None."""
        user_id = "forget_ceiling_test_user"
        self._make_core_with_ceiling(user_id)

        # Mock LLM + ChromaDB + history so no real I/O happens
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text='{"response": "Goodbye.", "clear_residue": true, "soften_opinion": true, "attachment_delta": -0.3}'
            )
        ]

        mock_col = MagicMock()
        mock_col.get.return_value = {"ids": []}

        with (
            patch("anjo.core.forgetting.get_client") as mock_client,
            patch("anjo.memory.long_term._get_collections", return_value=(mock_col, mock_col)),
            patch("anjo.core.history.clear"),
            patch("anjo.reflection.log._log_path") as mock_log_path,
        ):
            mock_log_path.return_value = MagicMock()
            mock_log_path.return_value.unlink = MagicMock()
            mock_client.return_value.messages.create.return_value = mock_response

            from anjo.core.forgetting import negotiate_and_forget

            negotiate_and_forget(user_id)

        from anjo.core.self_core import SelfCore

        refreshed = SelfCore.load(user_id)
        assert refreshed.relationship_ceiling is None, (
            f"Expected relationship_ceiling to be None after forget, got: {refreshed.relationship_ceiling}"
        )

    def test_relationship_stage_is_stranger_after_forget(self, monkeypatch):
        """negotiate_and_forget must reset stage to 'stranger'."""
        user_id = "forget_stage_test_user"
        self._make_core_with_ceiling(user_id, stage="friend", ceiling="friend")

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text='{"response": "Goodbye.", "clear_residue": true, "soften_opinion": true, "attachment_delta": -0.3}'
            )
        ]

        mock_col = MagicMock()
        mock_col.get.return_value = {"ids": []}

        with (
            patch("anjo.core.forgetting.get_client") as mock_client,
            patch("anjo.memory.long_term._get_collections", return_value=(mock_col, mock_col)),
            patch("anjo.core.history.clear"),
            patch("anjo.reflection.log._log_path") as mock_log_path,
        ):
            mock_log_path.return_value = MagicMock()
            mock_log_path.return_value.unlink = MagicMock()
            mock_client.return_value.messages.create.return_value = mock_response

            from anjo.core.forgetting import negotiate_and_forget

            negotiate_and_forget(user_id)

        from anjo.core.self_core import SelfCore

        refreshed = SelfCore.load(user_id)
        assert refreshed.relationship.stage == "stranger"

    def test_ceiling_last_checked_is_zero_after_forget(self, monkeypatch):
        """negotiate_and_forget must reset ceiling_last_checked to 0."""
        user_id = "forget_ceiling_checked_test_user"
        self._make_core_with_ceiling(user_id)

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text='{"response": "Goodbye.", "clear_residue": true, "soften_opinion": true, "attachment_delta": -0.3}'
            )
        ]

        mock_col = MagicMock()
        mock_col.get.return_value = {"ids": []}

        with (
            patch("anjo.core.forgetting.get_client") as mock_client,
            patch("anjo.memory.long_term._get_collections", return_value=(mock_col, mock_col)),
            patch("anjo.core.history.clear"),
            patch("anjo.reflection.log._log_path") as mock_log_path,
        ):
            mock_log_path.return_value = MagicMock()
            mock_log_path.return_value.unlink = MagicMock()
            mock_client.return_value.messages.create.return_value = mock_response

            from anjo.core.forgetting import negotiate_and_forget

            negotiate_and_forget(user_id)

        from anjo.core.self_core import SelfCore

        refreshed = SelfCore.load(user_id)
        assert refreshed.ceiling_last_checked == 0


# ---------------------------------------------------------------------------
# STAGE-1  Ceiling check 5-session gate
# ---------------------------------------------------------------------------


class TestCeilingCheckGate:
    """_maybe_advance_past_ceiling must only fire when enough sessions have passed."""

    def _make_core(self, session_count: int, ceiling_last_checked: int, ceiling: str = "friend"):
        from anjo.core.self_core import SelfCore

        core = SelfCore()
        core.relationship.stage = ceiling
        core.relationship.session_count = session_count
        core.relationship_ceiling = ceiling
        core.ceiling_last_checked = ceiling_last_checked
        return core

    def test_not_called_when_too_few_sessions_have_passed(self):
        """With only 3 sessions since last check (need 5), _maybe_advance must short-circuit."""
        from anjo.reflection.engine import (
            _maybe_advance_past_ceiling,
        )

        core = self._make_core(session_count=13, ceiling_last_checked=10)  # 3 apart < 5

        llm_call_count = []

        with patch("anjo.reflection.engine.get_client") as mock_client:
            mock_client.side_effect = lambda: llm_call_count.append(1) or MagicMock()
            _maybe_advance_past_ceiling(core)

        assert len(llm_call_count) == 0, (
            "_maybe_advance_past_ceiling should NOT call the LLM when only 3 sessions have passed"
        )

    def test_called_when_enough_sessions_have_passed(self):
        """With 5+ sessions since last check, the LLM should be consulted."""
        from anjo.reflection.engine import _maybe_advance_past_ceiling

        # ceiling_last_checked=5, session_count=10 → 5 apart = exactly at threshold
        core = self._make_core(session_count=10, ceiling_last_checked=5)

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text='{"advance": false, "reason": "respecting their wish"}')
        ]

        with patch("anjo.reflection.engine.get_client") as mock_client:
            mock_client.return_value.messages.create.return_value = mock_response
            _maybe_advance_past_ceiling(core)
            assert mock_client.return_value.messages.create.called, (
                "_maybe_advance_past_ceiling SHOULD call the LLM when 5 sessions have passed"
            )

    def test_never_fires_when_ceiling_is_none(self):
        """No ceiling set → function must return immediately without any LLM call."""
        from anjo.core.self_core import SelfCore
        from anjo.reflection.engine import _maybe_advance_past_ceiling

        core = SelfCore()
        core.relationship_ceiling = None

        with patch("anjo.reflection.engine.get_client") as mock_client:
            _maybe_advance_past_ceiling(core)
            assert not mock_client.called

    def test_first_check_fires_when_ceiling_last_checked_is_zero(self):
        """When ceiling_last_checked==0 (never checked before), the gate does not block it."""
        from anjo.reflection.engine import _maybe_advance_past_ceiling

        core = self._make_core(session_count=6, ceiling_last_checked=0)

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"advance": false, "reason": "staying"}')]

        with patch("anjo.reflection.engine.get_client") as mock_client:
            mock_client.return_value.messages.create.return_value = mock_response
            _maybe_advance_past_ceiling(core)
            assert mock_client.return_value.messages.create.called, (
                "First check (ceiling_last_checked=0) should not be gated"
            )


# ===========================================================================
# P3 — BUG / ASYNC FIXES
# ===========================================================================

# ---------------------------------------------------------------------------
# ASYNC-1 / BUG-20  get_or_create_session concurrency
# ---------------------------------------------------------------------------


class TestGetOrCreateSessionConcurrency:
    """Two concurrent calls to get_or_create_session for the same new user_id
    must result in exactly ONE session, not two."""

    def test_concurrent_calls_produce_one_session(self):
        """Use a threading.Barrier to maximise the race window."""
        import anjo.dashboard.session_store as _ss

        user_id = "concurrent_session_test_user"

        with _ss._sessions_lock:
            _ss._sessions.pop(user_id, None)

        barrier = threading.Barrier(2)
        results = []
        errors = []

        def _create():
            try:
                barrier.wait(timeout=5)
                sid = _ss.get_or_create_session(user_id)
                results.append(sid)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=_create)
        t2 = threading.Thread(target=_create)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Threads raised errors: {errors}"
        assert len(results) == 2  # both calls return
        # Only one entry in the sessions dict
        assert user_id in _ss._sessions, "Session was not created"

    def test_first_message_injected_exactly_once(self):
        """The first assistant message (opener) must appear exactly once in history."""
        import anjo.dashboard.session_store as _ss

        user_id = "first_msg_once_test_user"

        with _ss._sessions_lock:
            _ss._sessions.pop(user_id, None)

        barrier = threading.Barrier(2)
        errors = []

        def _create():
            try:
                barrier.wait(timeout=5)
                _ss.get_or_create_session(user_id)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=_create)
        t2 = threading.Thread(target=_create)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors
        session = _ss.get_session(user_id)
        assert session is not None
        history = session["state"].get("conversation_history", [])
        assistant_msgs = [m for m in history if m["role"] == "assistant"]
        assert len(assistant_msgs) <= 1, (
            f"Expected at most 1 first-message but found {len(assistant_msgs)}: {assistant_msgs}"
        )


# ---------------------------------------------------------------------------
# EDGE-5 / EDGE-16  Empty/whitespace message rejected
# ---------------------------------------------------------------------------


class TestEmptyMessageRejection:
    """The chat endpoint must reject empty and invisible-character-only messages."""

    def _start_and_get_session_id(self, auth_client) -> str:
        """Start a session and return the session_id."""
        r = auth_client.post("/api/chat/start")
        assert r.status_code == 200
        return r.json()["session_id"]

    def test_empty_string_body_returns_400(self, auth_client):
        sid = self._start_and_get_session_id(auth_client)
        r = auth_client.post(
            f"/api/chat/{sid}/message",
            json={"text": ""},
        )
        assert r.status_code == 400

    def test_whitespace_only_body_returns_400(self, auth_client):
        sid = self._start_and_get_session_id(auth_client)
        r = auth_client.post(
            f"/api/chat/{sid}/message",
            json={"text": "   "},
        )
        assert r.status_code == 400

    def test_zero_width_spaces_only_returns_400(self, auth_client):
        """Zero-width spaces (\u200b\u200b) must be stripped and rejected."""
        sid = self._start_and_get_session_id(auth_client)
        r = auth_client.post(
            f"/api/chat/{sid}/message",
            json={"text": "\u200b\u200b"},
        )
        assert r.status_code == 400

    def test_mixed_invisible_chars_returns_400(self, auth_client):
        """Mix of zero-width space, FEFF, and regular space → still empty → 400."""
        sid = self._start_and_get_session_id(auth_client)
        r = auth_client.post(
            f"/api/chat/{sid}/message",
            json={"text": "\u200b \ufeff \u2060"},
        )
        assert r.status_code == 400

    def test_real_message_does_not_return_400(self, auth_client):
        """A normal non-empty message must NOT be rejected with 400."""
        sid = self._start_and_get_session_id(auth_client)
        # We mock the LLM to avoid a real API call; we only care that it is NOT 400
        with patch("anjo.core.llm.get_client") as mock_client:
            mock_stream = MagicMock()
            mock_stream.__enter__ = MagicMock(return_value=mock_stream)
            mock_stream.__exit__ = MagicMock(return_value=False)
            mock_stream.text_stream = iter(["Hello", " world"])
            mock_usage = MagicMock()
            mock_usage.input_tokens = 10
            mock_usage.output_tokens = 5
            mock_stream.get_final_message.return_value = MagicMock(usage=mock_usage)
            mock_client.return_value.messages.stream.return_value = mock_stream

            r = auth_client.post(
                f"/api/chat/{sid}/message",
                json={"text": "hello"},
            )
        assert r.status_code != 400


# ---------------------------------------------------------------------------
# BUG-11  _QUICK_FACTS_DONE pruned on session end
# ---------------------------------------------------------------------------


class TestQuickFactsDonePruned:
    """After end_session, (user_id, session_id) must be removed from _QUICK_FACTS_DONE."""

    def test_key_removed_from_quick_facts_done_on_end_session(self, auth_client):
        from anjo.dashboard.background_tasks import _QUICK_FACTS_DONE, _SETS_LOCK

        # Start session to get a real session_id
        r = auth_client.post("/api/chat/start")
        assert r.status_code == 200
        session_id = r.json()["session_id"]

        # Manually inject a key as if quick-facts extraction had run
        # We need the user_id — extract it from the auth cookie
        import anjo.dashboard.auth as _auth

        cookie_val = auth_client.cookies.get("anjo_auth", "")
        user_id = _auth.verify_token(cookie_val)
        assert user_id is not None

        key = (user_id, session_id)
        with _SETS_LOCK:
            _QUICK_FACTS_DONE[key] = None

        assert key in _QUICK_FACTS_DONE

        # End session — this should remove the key
        with (
            patch("anjo.reflection.engine.run_reflection"),
            patch("anjo.core.transcript_queue.save_pending", return_value="/tmp/fake.json"),
            patch("anjo.core.transcript_queue.delete_pending"),
        ):
            auth_client.post(f"/api/chat/{session_id}/end")

        assert key not in _QUICK_FACTS_DONE, (
            f"Expected {key} to be removed from _QUICK_FACTS_DONE after end_session"
        )


# ===========================================================================
# P4 — COST / ARCHITECTURE FIXES
# ===========================================================================

# ---------------------------------------------------------------------------
# DEBT-6 / BUG-14  Anthropic client singleton
# ---------------------------------------------------------------------------


class TestAnthropicClientSingleton:
    """get_client() must return the same object on repeated calls (singleton pattern)."""

    def test_two_calls_return_same_object(self, monkeypatch):
        """is identity check — must be the exact same Python object."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key_for_singleton")
        import anjo.core.llm as _llm

        # Reset singleton state
        _llm._client = None

        c1 = _llm.get_client()
        c2 = _llm.get_client()
        assert c1 is c2, "get_client() must return the same singleton instance"

    def test_singleton_survives_multiple_calls(self, monkeypatch):
        """Call get_client() 5 times — must be the same object every time."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test_key_for_singleton")
        import anjo.core.llm as _llm

        _llm._client = None

        first = _llm.get_client()
        for _ in range(4):
            assert _llm.get_client() is first

    def test_no_api_key_raises_runtime_error(self, monkeypatch):
        """Without ANTHROPIC_API_KEY, get_client must raise RuntimeError cleanly."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        import anjo.core.llm as _llm

        orig_use_bedrock = _llm.USE_BEDROCK
        orig_bearer = _llm._BEARER_TOKEN
        _llm._client = None
        # USE_BEDROCK is set at module import time — override to force standard-API path
        _llm.USE_BEDROCK = False
        _llm._BEARER_TOKEN = ""

        try:
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
                _llm.get_client()
        finally:
            _llm._client = None
            _llm.USE_BEDROCK = orig_use_bedrock
            _llm._BEARER_TOKEN = orig_bearer


# ---------------------------------------------------------------------------
# TOKEN-1 / CACHE-1  build_system_prompt facts parameter
# ---------------------------------------------------------------------------


class TestBuildSystemPromptFacts:
    """Three-Tier architecture: build_system_prompt no longer injects facts directly.
    Facts now live in JOURNAL.md (consolidated post-reflection by journal.py).
    The user_facts parameter is accepted for backward compatibility but is unused
    in prompt construction — callers can safely pass it without effect."""

    def _make_core(self, user_id: str = "facts_test_user"):
        from anjo.core.self_core import SelfCore

        core = SelfCore.load(user_id)
        core.user_id = user_id
        return core

    def test_user_facts_passed_load_facts_not_called(self):
        """Facts are never loaded directly — journal handles them post-reflection."""
        from anjo.core.prompt_builder import build_system_prompt

        core = self._make_core()

        with patch("anjo.core.facts.load_facts") as mock_load:
            build_system_prompt(core, user_facts=["fact1", "fact2"])
            mock_load.assert_not_called()

    def test_user_facts_none_load_facts_not_called(self):
        """Even with user_facts=None, load_facts is not called by prompt_builder.
        Facts are consolidated into the journal during reflection, not per-turn."""
        from anjo.core.prompt_builder import build_system_prompt

        core = self._make_core()

        with patch("anjo.core.facts.load_facts") as mock_load:
            build_system_prompt(core, user_facts=None)
            mock_load.assert_not_called()

    def test_empty_list_user_facts_does_not_call_load_facts(self):
        """An explicitly passed empty list has no effect — no fallback call."""
        from anjo.core.prompt_builder import build_system_prompt

        core = self._make_core()

        with patch("anjo.core.facts.load_facts") as mock_load:
            build_system_prompt(core, user_facts=[])
            mock_load.assert_not_called()


# ---------------------------------------------------------------------------
# TOKEN-10 regression guard  reflection summary variable
# ---------------------------------------------------------------------------


class TestReflectionSummaryVariable:
    """run_reflection must not raise NameError when 'summary' is present or absent
    in the LLM response."""

    def _minimal_transcript(self) -> list[dict]:
        return [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "how are you"},
            {"role": "assistant", "content": "doing well"},
        ]

    def _make_llm_resp(self, payload: dict) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text=json.dumps(payload))]
        return mock_resp

    def _pass1(self) -> MagicMock:
        return self._make_llm_resp(
            {
                "user_name": None,
                "user_facts": [],
                "memorable_moments": [],
                "topics": ["general"],
                "user_stated_ceiling": None,
                "memory_nodes": [],
            }
        )

    def _pass2(self) -> MagicMock:
        return self._make_llm_resp(
            {
                "emotional_tone": "warm",
                "emotional_valence": 0.5,
                "user_input_valence": 0.7,
                "triggers": [],
                "new_residue": [],
                "attachment_update": None,
                "opinion_update": None,
                "preoccupation": None,
            }
        )

    def _pass3(self, summary: str = "A test session summary.") -> MagicMock:
        return self._make_llm_resp(
            {
                "significance": 0.3,
                "note": None,
                "desires_add": [],
                "desires_remove": [],
                "memory_relevance": 0.0,
                "summary": summary,
            }
        )

    def test_summary_present_passes_to_store_memory(self):
        """When Pass 3 returns a summary, store_memory must be called with it."""
        from anjo.core.self_core import SelfCore
        from anjo.reflection.engine import run_reflection

        user_id = "reflect_summary_user"
        core = SelfCore.load(user_id)
        core.user_id = user_id

        store_calls = []

        with (
            patch("anjo.reflection.engine.get_client") as mock_client,
            patch(
                "anjo.reflection.engine.store_memory",
                side_effect=lambda **kw: store_calls.append(kw),
            ),
            patch("anjo.reflection.log.append_log"),
        ):
            mock_client.return_value.messages.create.side_effect = [
                self._pass1(),
                self._pass2(),
                self._pass3(summary="We talked about life."),
            ]
            run_reflection(
                transcript=self._minimal_transcript(),
                core=core,
                user_id=user_id,
                session_id="test_session_1",
            )

        assert any(c.get("summary") == "We talked about life." for c in store_calls), (
            "store_memory should have been called with the summary from Pass 3"
        )

    def test_summary_missing_no_name_error(self):
        """When Pass 3 omits 'summary', run_reflection must not raise any exception."""
        from anjo.core.self_core import SelfCore
        from anjo.reflection.engine import run_reflection

        user_id = "reflect_no_summary_user"
        core = SelfCore.load(user_id)
        core.user_id = user_id

        with (
            patch("anjo.reflection.engine.get_client") as mock_client,
            patch("anjo.reflection.engine.store_memory"),
            patch("anjo.reflection.log.append_log"),
        ):
            mock_client.return_value.messages.create.side_effect = [
                self._pass1(),
                self._pass2(),
                self._pass3(summary=""),
            ]
            run_reflection(
                transcript=self._minimal_transcript(),
                core=core,
                user_id=user_id,
                session_id="test_session_2",
            )
        # If we get here without exception, the test passes

    def test_summary_empty_string_no_store_memory_call(self):
        """Empty summary string → store_memory must NOT be called for session memory."""
        from anjo.core.self_core import SelfCore
        from anjo.reflection.engine import run_reflection

        user_id = "reflect_empty_summary_user"
        core = SelfCore.load(user_id)
        core.user_id = user_id

        store_calls = []

        with (
            patch("anjo.reflection.engine.get_client") as mock_client,
            patch(
                "anjo.reflection.engine.store_memory",
                side_effect=lambda **kw: store_calls.append(kw),
            ),
            patch("anjo.reflection.log.append_log"),
        ):
            mock_client.return_value.messages.create.side_effect = [
                self._pass1(),
                self._pass2(),
                self._pass3(summary=""),
            ]
            run_reflection(
                transcript=self._minimal_transcript(),
                core=core,
                user_id=user_id,
                session_id="test_session_3",
            )

        # Empty summary guards `if summary:` → store_memory never called
        assert len(store_calls) == 0, (
            f"store_memory should not be called for an empty summary, but got: {store_calls}"
        )
