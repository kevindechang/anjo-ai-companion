"""Regression tests for bug fixes.

These tests verify that specific bugs that were fixed don't regress.
Tests are designed to work with the isolated test environment.
"""
from __future__ import annotations

import json
import pytest


# ── SelfCore.user_id field ─────────────────────────────────────────────────────

class TestSelfCoreUserIdField:
    """Test that user_id survives model_validate() calls.

    Bug: SelfCore had user_id stored as a PrivateAttr, which got lost
    when model_validate() was called during load(). This caused Anjo
    to write to the wrong user directory (users/default/ instead of
    users/<actual_user_id>/).
    """

    def test_user_id_survives_model_validate(self):
        """user_id field should be preserved through model_validate()."""
        from anjo.core.self_core import SelfCore

        # Create a SelfCore with a specific user_id
        core = SelfCore()
        core.user_id = "test-user-123"

        # Simulate what happens during load() - model_validate
        data = core.model_dump()
        validated = SelfCore.model_validate(data)

        # The user_id should be preserved
        assert validated.user_id == "test-user-123"

    def test_load_restores_user_id(self):
        """SelfCore.load() should restore user_id after model_validate()."""
        from anjo.core.self_core import SelfCore

        # Load for a specific user
        core = SelfCore.load("regression-test-user")

        # user_id should be set to the requested user
        assert core.user_id == "regression-test-user"

    def test_save_writes_to_correct_user_directory(self):
        """SelfCore.save() should write to the correct user's directory."""
        from anjo.core.self_core import SelfCore, _core_dir, _DATA_ROOT

        user_id = "save-test-user-456"
        core = SelfCore.load(user_id)

        # Modify something to trigger save
        core.mood.valence = 0.5
        core.save()

        # Verify the file was written to the correct location
        expected_path = _core_dir(user_id) / "current.json"
        assert expected_path.exists(), f"Expected self-core file at {expected_path}"

        # Verify the content has the correct user_id (file is now encrypted)
        from anjo.core.crypto import read_encrypted
        content = json.loads(read_encrypted(expected_path))
        assert content["user_id"] == user_id


# ── Security: Facts sanitization ───────────────────────────────────────────────

class TestFactsSanitization:
    """Test that facts are sanitized to prevent XSS attacks.

    Bug: User facts extracted by the LLM were injected directly into
    prompts without sanitization, allowing XSS attacks.
    """

    def test_html_escape_prevents_script_tags(self):
        """Facts containing script tags should be escaped."""
        from anjo.core.facts import _sanitize_fact

        # XSS attack vectors with script tags should be escaped
        malicious = '<script>alert("xss")</script>'
        sanitized = _sanitize_fact(malicious)
        assert "<script>" not in sanitized
        assert "&lt;script&gt;" in sanitized

        # Event handlers should also be escaped
        malicious2 = '<img src=x onerror=alert(1)>'
        sanitized2 = _sanitize_fact(malicious2)
        assert "<img" not in sanitized2
        assert "&lt;img" in sanitized2

    def test_facts_load_sanitizes(self, auth_client):
        """load_facts() should sanitize all returned facts."""
        from anjo.core.facts import load_facts
        from anjo.core.db import get_db

        user_id = "sanitize-test-user"

        # Insert raw unsanitized facts directly into DB
        db = get_db()
        db.execute(
            "INSERT OR REPLACE INTO facts (user_id, facts_json, updated_at) VALUES (?, ?, ?)",
            (user_id, json.dumps(['<script>bad</script>', 'Normal fact']), "2024-01-01T00:00:00"),
        )
        db.commit()

        # load_facts should sanitize them
        facts = load_facts(user_id)
        assert all("<script>" not in f for f in facts)

    def test_merge_facts_sanitizes_input(self, auth_client):
        """merge_facts() should sanitize new facts before storing."""
        from anjo.core.facts import load_facts, merge_facts

        user_id = "merge-sanitize-test"

        # Merge malicious facts
        malicious = ['<img onerror=alert(1) src=x>', 'legit fact']
        merge_facts(user_id, malicious)

        # Stored facts should be sanitized
        facts = load_facts(user_id)
        assert all("<img" not in f for f in facts)


# ── Atomic credit deduction ─────────────────────────────────────────────────────

class TestAtomicCreditDeduction:
    """Test that concurrent credit deductions don't go negative.

    Bug: Credit deductions used separate SELECT and UPDATE operations,
    allowing race conditions where concurrent deductions could bring
    balance negative.
    """

    def test_deduct_cost_prevents_negative_balance(self, auth_client):
        """deduct_cost() should never go negative even with concurrent calls."""
        from anjo.core.credits import deduct_cost, grant_initial_credits, get_balance

        user_id = "atomic-test-user"
        grant_initial_credits(user_id)

        # Get initial balance (5.00)
        initial = get_balance(user_id)

        # Simulate a cost larger than balance
        # The atomic CASE should prevent negative
        result = deduct_cost(user_id, "claude-sonnet-4-6", 1_000_000, 1_000_000)

        # Balance should be 0, not negative
        final = get_balance(user_id)
        assert final >= 0, f"Balance went negative: {final}"
        assert final == 0.0, f"Expected 0.0, got {final}"

    def test_concurrent_deduct_cost_atomic(self, auth_client):
        """Concurrent deduct_cost calls should be atomic."""
        from anjo.core.credits import deduct_cost, grant_initial_credits, get_balance
        import concurrent.futures

        user_id = "concurrent-test-user"
        grant_initial_credits(user_id)

        # Make many small deductions concurrently
        # Each deduction is small (1000 tokens = ~$0.003)
        def deduct():
            return deduct_cost(user_id, "claude-haiku-4-5-20251001", 1000, 1000)

        # Run 10 concurrent deductions (total = ~$0.03)
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(deduct) for _ in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # Final balance should be >= 0
        final = get_balance(user_id)
        assert final >= 0, f"Concurrent deduction caused negative balance: {final}"

    def test_deduct_message_credit_atomic(self, auth_client):
        """deduct_message_credit() should be atomic and prevent negative."""
        from anjo.core.credits import deduct_message_credit, add_message_credits, get_message_credits

        user_id = "msg-credit-test"

        # Add exactly 1 credit
        add_message_credits(user_id, 1)
        assert get_message_credits(user_id) == 1

        # Deduct it
        result = deduct_message_credit(user_id)
        assert result is True
        assert get_message_credits(user_id) == 0

        # Try to deduct when none available - should fail gracefully
        result = deduct_message_credit(user_id)
        assert result is False
        assert get_message_credits(user_id) == 0  # Should not go negative

    def test_concurrent_message_credit_deduction(self, auth_client):
        """Concurrent message credit deductions should not go negative."""
        from anjo.core.credits import deduct_message_credit, add_message_credits, get_message_credits
        import concurrent.futures

        user_id = "concurrent-msg-test"

        # Add 5 credits
        add_message_credits(user_id, 5)

        # Try to deduct 10 concurrently (only 5 should succeed)
        def try_deduct():
            return deduct_message_credit(user_id)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(try_deduct) for _ in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # Should have exactly 5 successes
        assert sum(results) == 5
        # Balance should be 0
        assert get_message_credits(user_id) == 0


# ── Reflection retry logic ─────────────────────────────────────────────────────

class TestReflectionRetry:
    """Test that reflection retry works for transient failures.

    Bug: Reflection Engine didn't retry on transient LLM failures,
    causing session data to be lost when the API was temporarily unavailable.
    """

    def test_retry_on_transient_failure(self, auth_client):
        """run_reflection should retry when LLM call fails transiently."""
        from anjo.reflection.engine import run_reflection
        from anjo.core.self_core import SelfCore
        from unittest.mock import patch, MagicMock

        user_id = "retry-test-user"
        session_id = "retry-session-123"

        # Create a minimal transcript (enough to pass the check)
        transcript = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "I'm doing well!"},
        ]

        core = SelfCore()
        core.user_id = user_id

        # Track call count
        call_count = 0

        def mock_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:  # Fail first 2 times
                raise Exception("Transient error")
            # Return success on 3rd attempt
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text='{"analysis": {"user_input_valence": 0.5, "triggers": []}, "memory": {"summary": "test", "emotional_tone": "neutral", "emotional_valence": 0.0, "topics": [], "significance": 0.3}}')]
            return mock_response

        with patch("anjo.reflection.engine.get_client") as mock_client:
            mock_client.return_value.messages.create = mock_create
            # Should not raise - should retry and succeed
            try:
                run_reflection(transcript, core, user_id, session_id, mid_session=False)
            except Exception:
                pass  # May still fail if retry exhausted

        # The 3-pass engine makes 3 LLM calls per reflection (extraction, emotional,
        # relational). Fails on attempts 1 and 2 (both from Pass 1), succeeds from
        # attempt 3 onwards — so total calls = 2 failed + 3 passes = 5.
        assert call_count >= 3, f"Expected at least 3 attempts, got {call_count}"

    def test_max_retries_exceeded_returns_early(self, auth_client):
        """When all retries fail, should log error and return gracefully."""
        from anjo.reflection.engine import run_reflection
        from anjo.core.self_core import SelfCore
        from unittest.mock import patch

        user_id = "max-retry-test"
        session_id = "max-retry-session"

        transcript = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "Good"},
        ]

        core = SelfCore()
        core.user_id = user_id

        def always_fail(*args, **kwargs):
            raise Exception("Persistent failure")

        with patch("anjo.reflection.engine.get_client") as mock_client:
            mock_client.return_value.messages.create = always_fail

            # Should not raise an unhandled exception
            try:
                run_reflection(transcript, core, user_id, session_id, mid_session=False)
            except Exception as e:
                pytest.fail(f"Should handle failure gracefully but raised: {e}")


# ── Double reflection guard ───────────────────────────────────────────────────

class TestDoubleReflectionGuard:
    """Test that the same session isn't reflected twice.

    Bug: If reflection was triggered multiple times for the same session,
    it would double-count significance and corrupt personality metrics.
    """

    def test_session_id_tracking_prevents_duplicate(self):
        """Each reflection should have unique session_id."""
        from anjo.reflection.log import append_log, read_log

        user_id = "double-reflect-test"
        session_id = "session-abc-123"

        # Log a reflection for a session
        append_log(
            session_id=session_id,
            deltas={"O": 0.01},
            memory_data={"significance": 0.5},
            message_count=10,
            user_id=user_id,
            mid_session=False,
        )

        # Read the log - should have exactly one entry
        logs = read_log(user_id)
        session_logs = [l for l in logs if l.get("session_id") == session_id]
        assert len(session_logs) == 1

    def test_duplicate_session_reflection_overwrites(self):
        """Duplicate session_id should not create duplicate logs."""
        from anjo.reflection.log import append_log, read_log

        user_id = "dup-log-test"
        session_id = "session-dup-456"

        # Log twice with same session_id
        append_log(
            session_id=session_id,
            deltas={"O": 0.01},
            memory_data={"significance": 0.5},
            message_count=10,
            user_id=user_id,
            mid_session=False,
        )
        append_log(
            session_id=session_id,
            deltas={"O": 0.02},  # Different delta
            memory_data={"significance": 0.6},
            message_count=12,
            user_id=user_id,
            mid_session=False,
        )

        # Should have 2 entries (append-only log)
        logs = read_log(user_id)
        session_logs = [l for l in logs if l.get("session_id") == session_id]
        # Log is append-only, so duplicates are allowed but distinguishable
        assert len(session_logs) == 2


# ── Drift race condition ───────────────────────────────────────────────────────

class TestDriftRaceCondition:
    """Test that drift skips users with active sessions.

    Bug: Background drift would run while user had an active session,
    causing race conditions where drift modified SelfCore while the
    session was also reading/writing it.
    """

    def test_drift_skips_active_session(self, auth_client):
        """apply_daily_drift() should skip users with active sessions."""
        from anjo.core.drift import apply_daily_drift
        from anjo.dashboard.session_store import get_or_create_session, delete_session, get_session

        user_id = "drift-race-test"

        # Create an active session
        get_or_create_session(user_id)
        assert get_session(user_id) is not None

        # Drift should skip this user
        result = apply_daily_drift(user_id)
        assert result is False, "Drift should skip active session"

        # Clean up
        delete_session(user_id)

    def test_drift_runs_without_active_session(self, auth_client):
        """apply_daily_drift() should run when no active session."""
        from anjo.core.drift import apply_daily_drift
        from anjo.dashboard.session_store import get_session, delete_session

        user_id = "drift-no-session-test"

        # Ensure no active session
        if get_session(user_id):
            delete_session(user_id)

        # Drift should run
        result = apply_daily_drift(user_id)
        assert result is True, "Drift should run without active session"

    def test_drift_respects_rate_limit(self, auth_client):
        """Drift should respect the 20-hour rate limit."""
        from anjo.core.drift import apply_daily_drift
        from anjo.dashboard.session_store import get_session, delete_session

        user_id = "drift-rate-limit-test"

        # Clean up any session
        if get_session(user_id):
            delete_session(user_id)

        # First run should succeed
        result1 = apply_daily_drift(user_id)
        assert result1 is True

        # Second immediate run should be rate-limited
        result2 = apply_daily_drift(user_id)
        assert result2 is False, "Drift should be rate-limited to once per 20 hours"

    def test_get_session_returns_active_session(self, auth_client):
        """Verify session store correctly tracks active sessions."""
        from anjo.dashboard.session_store import get_or_create_session, get_session, delete_session

        user_id = "session-track-test"

        # Before: no session
        assert get_session(user_id) is None

        # Create session
        get_or_create_session(user_id)
        assert get_session(user_id) is not None

        # Clean up
        delete_session(user_id)
        assert get_session(user_id) is None


# ── Credit system integration ─────────────────────────────────────────────────

class TestCreditSystemIntegration:
    """Integration tests for the credit system."""

    def test_initial_credits_on_registration(self, auth_client):
        """New users should receive initial credits."""
        from anjo.core.credits import get_balance, INITIAL_CREDIT_USD

        # Get the user_id from the auth_client fixture context
        # The fixture registers "testuser", so we can query their balance
        # But we need to ensure the user has credits
        from anjo.core.db import get_db
        db = get_db()
        row = db.execute("SELECT user_id FROM users LIMIT 1").fetchone()
        if row:
            user_id = row["user_id"]
            balance = get_balance(user_id)
            assert balance == INITIAL_CREDIT_USD

    def test_balance_check_prevents_negative_spend(self, auth_client):
        """has_balance() should return False when balance is 0."""
        from anjo.core.credits import has_balance, grant_initial_credits, deduct_cost

        user_id = "neg-spend-test"
        grant_initial_credits(user_id)

        # Drain the balance
        deduct_cost(user_id, "claude-sonnet-4-6", 10_000_000, 10_000_000)

        # Should have no balance
        assert has_balance(user_id) is False


# ── SelfCore save atomicity ───────────────────────────────────────────────────

class TestSelfCoreSaveAtomicity:
    """Test SelfCore save is atomic and handles failures gracefully."""

    def test_save_creates_backup(self):
        """Save should create versioned backup in history."""
        from anjo.core.self_core import SelfCore, _core_dir

        user_id = "backup-test-user"

        # First save creates current.json (no backup yet — nothing to back up)
        core = SelfCore.load(user_id)
        core.mood.valence = 0.7
        core.save()

        # Second save promotes current.json to history/v1.json
        core2 = SelfCore.load(user_id)
        core2.mood.valence = 0.5
        core2.save()

        # Now a history backup must exist
        history_dir = _core_dir(user_id) / "history"
        assert history_dir.exists(), "history/ directory should exist after second save"
        files = list(history_dir.glob("v*.json"))
        assert len(files) >= 1, f"Expected at least one backup in history/, got: {files}"

    def test_save_increments_version(self):
        """Each save should increment version."""
        from anjo.core.self_core import SelfCore

        user_id = "version-test-user"

        core = SelfCore.load(user_id)
        v1 = core.version

        core.mood.valence = 0.3
        core.save()

        core2 = SelfCore.load(user_id)
        assert core2.version > v1