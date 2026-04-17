"""Integration tests for the LangGraph production path and background tasks.

Tests cover:
  - pre_response_graph orchestration (silence, retrieve, no-retrieve paths)
  - Background task deduplication under concurrent access
  - occ_carry preservation across turns
  - AnjoState Pydantic model validation
"""
from __future__ import annotations

import threading

import pytest


# ── AnjoState Pydantic model ──────────────────────────────────────────────────


class TestAnjoState:
    """AnjoState should validate input and provide sensible defaults."""

    def test_defaults(self):
        from anjo.graph.state import AnjoState
        state = AnjoState()
        assert state.user_message == ""
        assert state.conversation_history == []
        assert state.should_respond is True
        assert state.should_retrieve is False
        assert state.occ_carry == {}
        assert state.seed_len == 0

    def test_from_dict(self):
        from anjo.graph.state import AnjoState
        state = AnjoState(**{"user_message": "hello", "user_id": "u1"})
        assert state.user_message == "hello"
        assert state.user_id == "u1"
        assert state.conversation_history == []

    def test_extra_fields_allowed(self):
        from anjo.graph.state import AnjoState
        state = AnjoState(**{"user_message": "hi", "custom_field": 42})
        assert state.custom_field == 42


# ── pre_response_graph ────────────────────────────────────────────────────────


class TestPreResponseGraph:
    """Test the production orchestration graph paths."""

    def test_graph_compiles(self):
        from anjo.graph.conversation_graph import pre_response_graph
        assert pre_response_graph is not None

    def test_full_graph_compiles(self):
        from anjo.graph.conversation_graph import conversation_graph
        assert conversation_graph is not None

    def test_silence_path(self, monkeypatch):
        """When gate_node returns should_respond=False, graph should stop early."""
        import anjo.graph.nodes as nodes

        def mock_gate(state):
            return {"intent": "CASUAL", "should_retrieve": False, "should_respond": False}

        monkeypatch.setattr(nodes, "gate_node", mock_gate)

        # Must rebuild AFTER patching so the graph captures the mock
        from anjo.graph.conversation_graph import build_pre_response_graph
        graph = build_pre_response_graph()
        result = graph.invoke({
            "user_message": "bye",
            "conversation_history": [{"role": "user", "content": "bye"}],
            "self_core": {},
            "user_id": "test",
        })
        assert result["should_respond"] is False
        # appraise_node should NOT have run (active_emotions unchanged from input)
        assert not result.get("active_emotions")

    def test_no_retrieve_path(self, monkeypatch):
        """When gate says no retrieval, skip retrieve_node."""
        import anjo.graph.nodes as nodes
        from anjo.core.self_core import SelfCore

        default_core = SelfCore.load("default").model_dump()

        def mock_gate(state):
            return {"intent": "CASUAL", "should_retrieve": False, "should_respond": True}

        def mock_appraise(state):
            return {"active_emotions": {"joy": 0.5}, "intent": "CASUAL",
                    "self_core": default_core, "occ_carry": {}}

        def mock_policy(state):
            return {"stance": "warm", "stance_directive": ""}

        retrieve_called = []

        def tracking_retrieve(state):
            retrieve_called.append(True)
            return {"retrieved_memories": []}

        monkeypatch.setattr(nodes, "gate_node", mock_gate)
        monkeypatch.setattr(nodes, "appraise_node", mock_appraise)
        monkeypatch.setattr(nodes, "policy_node", mock_policy)
        monkeypatch.setattr(nodes, "retrieve_node", tracking_retrieve)

        from anjo.graph.conversation_graph import build_pre_response_graph
        graph = build_pre_response_graph()
        result = graph.invoke({
            "user_message": "hello",
            "conversation_history": [],
            "self_core": default_core,
            "user_id": "test",
        })
        assert result["should_respond"] is True
        assert result["active_emotions"] == {"joy": 0.5}
        assert len(retrieve_called) == 0  # retrieve was skipped

    def test_retrieve_path(self, monkeypatch):
        """When gate says retrieve, retrieve_node runs."""
        import anjo.graph.nodes as nodes
        from anjo.core.self_core import SelfCore

        default_core = SelfCore.load("default").model_dump()

        def mock_gate(state):
            return {"intent": "CURIOSITY", "should_retrieve": True, "should_respond": True}

        def mock_retrieve(state):
            return {"retrieved_memories": [(0.9, "test memory")]}

        def mock_appraise(state):
            return {"active_emotions": {}, "intent": "CURIOSITY",
                    "self_core": default_core, "occ_carry": {}}

        def mock_policy(state):
            return {"stance": "engaged", "stance_directive": ""}

        monkeypatch.setattr(nodes, "gate_node", mock_gate)
        monkeypatch.setattr(nodes, "retrieve_node", mock_retrieve)
        monkeypatch.setattr(nodes, "appraise_node", mock_appraise)
        monkeypatch.setattr(nodes, "policy_node", mock_policy)

        from anjo.graph.conversation_graph import build_pre_response_graph
        graph = build_pre_response_graph()
        result = graph.invoke({
            "user_message": "what did I say last time?",
            "conversation_history": [],
            "self_core": default_core,
            "user_id": "test",
        })
        assert result["should_retrieve"] is True
        assert len(result["retrieved_memories"]) == 1


# ── occ_carry preservation ────────────────────────────────────────────────────


class TestOccCarry:
    """occ_carry should flow through the graph and be decayed, not reset."""

    def test_carry_flows_through(self, monkeypatch):
        """appraise_node receives occ_carry from previous turn and decays it."""
        from anjo.graph.nodes import appraise_node, _OCC_CARRY_DECAY
        from anjo.graph.state import AnjoState
        from anjo.core.self_core import SelfCore

        default_core = SelfCore.load("default")
        core_dict = default_core.model_dump()

        monkeypatch.setattr(
            "anjo.core.emotion.classify_intent_llm",
            lambda msg, **kw: "CASUAL",
        )

        state = AnjoState(
            user_message="hey",
            conversation_history=[{"role": "user", "content": "hey"}],
            self_core=core_dict,
            user_id="test",
            occ_carry={"reproach": 0.8},
        )
        result = appraise_node(state)
        # reproach should be decayed, not zero
        carry = result["occ_carry"]
        assert "reproach" in carry
        expected_min = 0.8 * _OCC_CARRY_DECAY["reproach"] * 0.9  # allow some tolerance
        assert carry["reproach"] >= expected_min


# ── Background task deduplication ─────────────────────────────────────────────


class TestBackgroundTaskDedup:
    """Deduplication tracking should be thread-safe."""

    def test_reflection_claim_idempotent(self):
        from anjo.dashboard.background_tasks import reflection_session_claim
        sid = "dedup-test-session"
        assert reflection_session_claim(sid) is True
        assert reflection_session_claim(sid) is False

    def test_reflection_claim_concurrent(self):
        """Only one thread wins the claim when racing."""
        from anjo.dashboard.background_tasks import reflection_session_claim

        results = []
        barrier = threading.Barrier(10)

        def claim():
            barrier.wait()
            results.append(reflection_session_claim(f"concurrent-{id(barrier)}"))

        threads = [threading.Thread(target=claim) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sum(results) == 1  # exactly one thread won

    def test_quick_facts_dedup(self):
        """quick_facts_extract fires once per (user_id, session_id)."""
        from anjo.dashboard.background_tasks import _QUICK_FACTS_DONE, _SETS_LOCK, _set_add

        key = ("user-dedup", "sess-dedup")
        with _SETS_LOCK:
            assert _set_add(_QUICK_FACTS_DONE, key, 2000) is True
            assert _set_add(_QUICK_FACTS_DONE, key, 2000) is False

    def test_bounded_set_eviction(self):
        """Bounded set evicts oldest entries when full."""
        from anjo.dashboard.background_tasks import _set_add
        import collections

        od: collections.OrderedDict = collections.OrderedDict()
        for i in range(5):
            _set_add(od, f"key-{i}", maxsize=3)

        assert len(od) == 3
        assert "key-0" not in od
        assert "key-1" not in od
        assert "key-4" in od

    def test_cleanup_removes_tracking(self):
        """cleanup_session_tracking removes entries from _QUICK_FACTS_DONE."""
        from anjo.dashboard.background_tasks import (
            _QUICK_FACTS_DONE, _SETS_LOCK, _set_add, cleanup_session_tracking,
        )

        key = ("cleanup-user", "cleanup-sess")
        with _SETS_LOCK:
            _set_add(_QUICK_FACTS_DONE, key, 2000)
        assert key in _QUICK_FACTS_DONE

        cleanup_session_tracking("cleanup-user", "cleanup-sess")
        assert key not in _QUICK_FACTS_DONE
