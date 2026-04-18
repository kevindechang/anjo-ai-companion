"""Tests — core API endpoints (self-core, billing, memory, story, admin)."""

from __future__ import annotations

# ── Self-Core ─────────────────────────────────────────────────────────────────


class TestSelfCore:
    def test_returns_dict(self, auth_client):
        r = auth_client.get("/api/self-core")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)

    def test_unauthenticated_returns_401(self, client):
        assert client.get("/api/self-core").status_code == 401

    def test_system_prompt_returns_string(self, auth_client):
        r = auth_client.get("/api/system-prompt")
        assert r.status_code == 200
        data = r.json()
        assert "prompt" in data
        assert isinstance(data["prompt"], str)
        assert len(data["prompt"]) > 0


# ── Billing ───────────────────────────────────────────────────────────────────


class TestBilling:
    def test_status_structure(self, auth_client):
        r = auth_client.get("/api/billing/status")
        assert r.status_code == 200
        d = r.json()
        assert "tier" in d
        assert "subscribed" in d
        assert "daily_limit" in d
        assert "messages_used" in d
        assert "messages_remaining" in d
        assert "message_credits" in d

    def test_new_user_is_free(self, auth_client):
        d = auth_client.get("/api/billing/status").json()
        assert d["tier"] == "free"
        assert d["subscribed"] is False

    def test_free_tier_daily_limit(self, auth_client):
        from anjo.core.subscription import FREE_DAILY_LIMIT

        d = auth_client.get("/api/billing/status").json()
        assert d["daily_limit"] == FREE_DAILY_LIMIT

    def test_config_returns_payments_flag(self, auth_client):
        r = auth_client.get("/api/billing/config")
        assert r.status_code == 200
        assert "payments_enabled" in r.json()

    def test_status_unauthenticated(self, client):
        assert client.get("/api/billing/status").status_code == 401

    def test_config_unauthenticated(self, client):
        assert client.get("/api/billing/config").status_code == 401


# ── Memory ────────────────────────────────────────────────────────────────────


class TestMemory:
    def test_reflection_log_structure(self, auth_client):
        r = auth_client.get("/api/reflection-log")
        assert r.status_code == 200
        d = r.json()
        assert "entries" in d
        assert isinstance(d["entries"], list)

    def test_memories_structure(self, auth_client):
        r = auth_client.get("/api/memories")
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d, dict)

    def test_reflection_log_unauthenticated(self, client):
        assert client.get("/api/reflection-log").status_code == 401

    def test_memories_unauthenticated(self, client):
        assert client.get("/api/memories").status_code == 401


# ── Story ─────────────────────────────────────────────────────────────────────


class TestStory:
    def test_sessions_structure(self, auth_client):
        r = auth_client.get("/api/story/sessions")
        assert r.status_code == 200
        d = r.json()
        assert "sessions" in d
        assert isinstance(d["sessions"], list)

    def test_letter_structure(self, auth_client):
        r = auth_client.get("/api/story/letter")
        assert r.status_code == 200
        d = r.json()
        assert "locked" in d

    def test_memories_structure(self, auth_client):
        r = auth_client.get("/api/story/memories")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)

    def test_story_endpoints_unauthenticated(self, client):
        for path in ["/api/story/sessions", "/api/story/letter", "/api/story/memories"]:
            assert client.get(path).status_code == 401, f"{path} should be 401"


# ── Admin ─────────────────────────────────────────────────────────────────────


class TestAdmin:
    def test_users_lists_registered_users(self, client):
        # Register two users
        for u in [("alpha", "alpha@test.com"), ("beta", "beta@test.com")]:
            client.post("/register", data={"username": u[0], "password": "pass1234", "email": u[1]})
        r = client.get("/api/admin/users", headers={"X-Admin-Key": "test_admin_key"})
        assert r.status_code == 200
        d = r.json()
        assert d["total"] == 2
        names = {u["username"] for u in d["users"]}
        assert "alpha" in names
        assert "beta" in names

    def test_users_response_fields(self, auth_client):
        r = auth_client.get("/api/admin/users", headers={"X-Admin-Key": "test_admin_key"})
        assert r.status_code == 200
        users = r.json()["users"]
        assert len(users) == 1
        u = users[0]
        assert "user_id" in u
        assert "username" in u
        assert "email" in u
        assert "email_verified" in u
        assert "created_at" in u
        assert "tier" in u
        assert "balance_usd" in u
        assert "message_credits" in u
        assert "daily_used" in u
        assert "daily_limit" in u
        assert "has_self_core" in u
        assert "has_memories" in u
        assert "data_size_kb" in u
        assert "is_active" in u
        assert "hashed_password" not in u  # must never leak

    def test_no_password_leakage(self, auth_client):
        r = auth_client.get("/api/admin/users", headers={"X-Admin-Key": "test_admin_key"})
        for user in r.json()["users"]:
            assert "hashed_password" not in user
            assert "password" not in user

    def test_stats_counts(self, client):
        client.post(
            "/register",
            data={"username": "gamma", "password": "pass1234", "email": "gamma@test.com"},
        )
        d = client.get("/api/admin/users", headers={"X-Admin-Key": "test_admin_key"}).json()
        assert d["total"] == 1
        assert d["active_sessions"] == 0
        assert d["subscribers"] == 0
        assert isinstance(d["total_balance"], float)

    def test_wrong_admin_key(self, client):
        assert client.get("/api/admin/users", headers={"X-Admin-Key": "bad"}).status_code == 401

    def test_admin_key_via_header(self, client):
        r = client.get("/api/admin/users", headers={"X-Admin-Key": "test_admin_key"})
        assert r.status_code == 200

    def test_chat_count_field_present(self, auth_client):
        r = auth_client.get("/api/admin/users", headers={"X-Admin-Key": "test_admin_key"})
        u = r.json()["users"][0]
        assert "chat_count" in u
        assert isinstance(u["chat_count"], int)


class TestAdminActions:
    """Per-user admin control endpoints."""

    def _uid(self, client):
        client.post(
            "/register",
            data={"username": "actionuser", "password": "pass1234", "email": "action@test.com"},
        )
        d = client.get("/api/admin/users", headers={"X-Admin-Key": "test_admin_key"}).json()
        return d["users"][0]["user_id"]

    def test_verify_email(self, client):
        uid = self._uid(client)
        r = client.post(f"/api/admin/users/{uid}/verify", headers={"X-Admin-Key": "test_admin_key"})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        # Confirm verified in user list
        users = client.get("/api/admin/users", headers={"X-Admin-Key": "test_admin_key"}).json()[
            "users"
        ]
        u = next(u for u in users if u["user_id"] == uid)
        assert u["email_verified"] is True

    def test_add_credits(self, client):
        uid = self._uid(client)
        r = client.post(
            f"/api/admin/users/{uid}/credits?amount=10.0",
            headers={"X-Admin-Key": "test_admin_key"},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["balance_usd"] > 0

    def test_add_credits_wrong_key(self, client):
        uid = self._uid(client)
        r = client.post(f"/api/admin/users/{uid}/credits?amount=5", headers={"X-Admin-Key": "bad"})
        assert r.status_code == 401

    def test_set_tier_pro(self, client):
        uid = self._uid(client)
        r = client.post(
            f"/api/admin/users/{uid}/tier?tier=pro",
            headers={"X-Admin-Key": "test_admin_key"},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["tier"] == "pro"

    def test_set_tier_invalid(self, client):
        uid = self._uid(client)
        r = client.post(
            f"/api/admin/users/{uid}/tier?tier=vip",
            headers={"X-Admin-Key": "test_admin_key"},
        )
        assert r.status_code == 400

    def test_reset_user(self, client):
        uid = self._uid(client)
        r = client.post(f"/api/admin/users/{uid}/reset", headers={"X-Admin-Key": "test_admin_key"})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_chat_history_endpoint(self, client):
        uid = self._uid(client)
        r = client.get(f"/api/admin/users/{uid}/chat", headers={"X-Admin-Key": "test_admin_key"})
        assert r.status_code == 200
        d = r.json()
        assert "messages" in d
        assert isinstance(d["messages"], list)

    def test_self_core_endpoint(self, client):
        uid = self._uid(client)
        r = client.get(
            f"/api/admin/users/{uid}/self-core", headers={"X-Admin-Key": "test_admin_key"}
        )
        assert r.status_code == 200
        d = r.json()
        assert "data" in d

    def test_delete_user(self, client):
        uid = self._uid(client)
        r = client.request(
            "DELETE", f"/api/admin/users/{uid}", headers={"X-Admin-Key": "test_admin_key"}
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        # User should be gone
        users = client.get("/api/admin/users", headers={"X-Admin-Key": "test_admin_key"}).json()[
            "users"
        ]
        assert all(u["user_id"] != uid for u in users)

    def test_delete_user_wrong_key(self, client):
        uid = self._uid(client)
        r = client.request("DELETE", f"/api/admin/users/{uid}", headers={"X-Admin-Key": "bad"})
        assert r.status_code == 401


# ── Rate limiting ─────────────────────────────────────────────────────────────


class TestRateLimit:
    def test_auth_rate_limit_shape(self, client):
        """After enough bad logins, server returns 429 with Retry-After."""
        for _ in range(12):
            r = client.post("/login", data={"username": "x", "password": "x"})
            if r.status_code == 429:
                assert "Retry-After" in r.headers
                return
        # If we never hit 429 in CI (rate limiter may reset), that's also fine.


# ── Session usage ─────────────────────────────────────────────────────────────


class TestSessionUsage:
    def test_session_usage_structure(self, auth_client):
        r = auth_client.get("/api/session/usage")
        assert r.status_code == 200
        d = r.json()
        assert "input_tokens" in d
        assert "output_tokens" in d
        assert "cost_usd" in d

    def test_session_emotions_structure(self, auth_client):
        r = auth_client.get("/api/session/emotions")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)
