"""Builds and compiles the Anjo conversation StateGraphs.

Two compiled graphs built from a shared base:

- ``pre_response_graph`` — used by the production SSE handler in chat_routes.py.
  Runs orchestration only (perceive → gate → retrieve? → appraise → policy → END).
  Streaming is handled separately after the graph completes.

- ``conversation_graph`` — used by the CLI (``anjo chat``) and tests.
  Extends the base with respond_node (blocking, non-streaming, no billing).
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

import anjo.graph.nodes as _nodes
from anjo.graph.state import AnjoState


def _route_after_gate(state: AnjoState) -> str:
    """After gate_node: silence, retrieve, or straight to appraise."""
    if not state.should_respond:
        return "end"
    if state.should_retrieve:
        return "retrieve"
    return "appraise"


def _build_base_graph() -> StateGraph:
    """Shared orchestration skeleton: perceive → gate → [retrieve →] appraise → policy."""
    graph = StateGraph(AnjoState)

    graph.add_node("perceive", _nodes.perceive_node)
    graph.add_node("gate", _nodes.gate_node)
    graph.add_node("retrieve", _nodes.retrieve_node)
    graph.add_node("appraise", _nodes.appraise_node)
    graph.add_node("policy", _nodes.policy_node)

    graph.set_entry_point("perceive")
    graph.add_edge("perceive", "gate")
    graph.add_conditional_edges(
        "gate",
        _route_after_gate,
        {
            "end": END,
            "retrieve": "retrieve",
            "appraise": "appraise",
        },
    )
    graph.add_edge("retrieve", "appraise")
    graph.add_edge("appraise", "policy")

    return graph


def build_pre_response_graph():
    """Orchestration-only: policy → END. Used by the production SSE handler."""
    graph = _build_base_graph()
    graph.add_edge("policy", END)
    return graph.compile()


def build_graph():
    """Full graph: policy → respond → END. CLI and test use only."""
    graph = _build_base_graph()
    graph.add_node("respond", _nodes.respond_node)
    graph.add_edge("policy", "respond")
    graph.add_edge("respond", END)
    return graph.compile()


# Compiled once on import
pre_response_graph = build_pre_response_graph()
conversation_graph = build_graph()
