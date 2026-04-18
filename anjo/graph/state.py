"""AnjoState — the data contract flowing through the LangGraph conversation graph.

Used by both the production SSE path (pre_response_graph in chat_routes.py)
and the CLI/test path (conversation_graph). Both paths share this state schema.

Pydantic BaseModel: nodes receive validated model instances with attribute access.
Graph invoke/ainvoke accepts dicts and returns dicts, so callers outside the
graph (chat_routes.py, session_store.py) continue to work with plain dicts.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AnjoState(BaseModel):
    # Current user input
    user_message: str = ""

    # Full conversation history for this session (role/content dicts for the API)
    conversation_history: list[dict] = Field(default_factory=list)

    # SelfCore as a plain dict (loaded once at session start, updated in-place)
    self_core: dict = Field(default_factory=dict)

    # Set by classify_node / gate_node
    should_retrieve: bool = False

    # Set by retrieve_node; empty list if retrieval was skipped
    retrieved_memories: list = Field(default_factory=list)

    # Set by respond_node
    assistant_response: str = ""

    # Logged-in user — set at session start, used for per-user data access
    user_id: str = ""

    # Session identifier — set at session start, used for logging and reflection
    session_id: str = ""

    # OCC emotional appraisal result for the current message (transient, not persisted)
    active_emotions: dict = Field(default_factory=dict)

    # Intent classified by appraise_node: ABUSE|VULNERABILITY|CHALLENGE|NEGLECT|CURIOSITY|APOLOGY|CASUAL
    intent: str = ""

    # Carried OCC emotions — persist across turns within a session with per-emotion decay.
    # Reproach from ABUSE doesn't vanish on the next message; it fades over several turns.
    occ_carry: dict = Field(default_factory=dict)

    # Set by silence_node / gate_node — if False, skip the LLM call entirely and send nothing.
    should_respond: bool = True

    # Accumulated input/output token counts for the session
    session_tokens: dict = Field(default_factory=lambda: {"input": 0, "output": 0})

    # Client-reported UTC offset in minutes (set by /chat/start, used in prompt_builder)
    tz_offset: int = 0

    # Number of messages at session start that are "seed" (history from prior session).
    # Transcript slicing uses this to exclude seed messages from reflection.
    seed_len: int = 0

    # Cached user facts loaded at session start (list of fact strings from SQLite)
    cached_user_facts: list[str] = Field(default_factory=list)

    # Cached globally trending topics (list of topic strings, updated hourly)
    cached_trending_topics: list[str] = Field(default_factory=list)

    # Set by policy_node — conversational stance for this turn
    stance: str = ""

    # Set by policy_node — directive text injected into prompt dynamic block
    stance_directive: str = ""

    model_config = {"extra": "allow"}
