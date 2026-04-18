"""Conditional edge functions for the Anjo conversation graph."""

from __future__ import annotations

from anjo.graph.state import AnjoState


def route_memory(state: AnjoState) -> str:
    """After classify_node: go to retrieve_node or skip straight to respond_node.

    NOTE: Only used by legacy classify_node path. The current graph uses
    gate_node with _route_after_gate in conversation_graph.py.
    """
    return "retrieve" if state.should_retrieve else "respond"
