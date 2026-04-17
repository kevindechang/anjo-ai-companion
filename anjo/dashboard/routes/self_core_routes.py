"""Self-Core and System Prompt API routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from anjo.dashboard.auth import get_current_user_id

router = APIRouter()


@router.get("/self-core")
def get_self_core(user_id: str = Depends(get_current_user_id)):
    from anjo.dashboard.session_store import get_self_core_safe
    from anjo.core.self_core import SelfCore
    core_dict = get_self_core_safe(user_id)
    if core_dict:
        return core_dict
    return SelfCore.load(user_id).model_dump()


@router.get("/system-prompt")
def get_system_prompt(user_id: str = Depends(get_current_user_id)):
    from anjo.dashboard.session_store import get_self_core_safe
    from anjo.core.self_core import SelfCore
    from anjo.core.prompt_builder import build_system_prompt
    core_dict = get_self_core_safe(user_id)
    if core_dict:
        core = SelfCore.model_validate(core_dict)
        core.user_id = user_id  # field restored after model_validate
    else:
        core = SelfCore.load(user_id)
    static_block, dynamic_block = build_system_prompt(core)
    return {"prompt": static_block + "\n\n" + dynamic_block}


@router.get("/session/emotions")
def get_session_emotions(user_id: str = Depends(get_current_user_id)):
    from anjo.dashboard.session_store import get_session_snapshot
    snapshot = get_session_snapshot(user_id)
    emotions = snapshot["state"].get("active_emotions", {}) if snapshot else {}
    return {"active_emotions": emotions}


_VALID_CEILINGS = {"acquaintance", "friend", "close", "intimate", "none"}

class CeilingRequest(BaseModel):
    ceiling: str  # "acquaintance" | "friend" | "close" | "intimate" | "none"

@router.post("/preferences/ceiling")
def set_relationship_ceiling(body: CeilingRequest, user_id: str = Depends(get_current_user_id)):
    if body.ceiling not in _VALID_CEILINGS:
        raise HTTPException(400, f"Invalid ceiling. Must be one of: {_VALID_CEILINGS}")
    from anjo.core.self_core import SelfCore
    from anjo.dashboard.session_store import get_session_snapshot, update_session_state
    core = SelfCore.load(user_id)
    core.relationship_ceiling = None if body.ceiling == "none" else body.ceiling
    core.save()
    # Also update live session so it takes effect immediately
    snapshot = get_session_snapshot(user_id)
    if snapshot:
        snapshot["state"]["self_core"]["relationship_ceiling"] = core.relationship_ceiling
        update_session_state(user_id, snapshot["state"])
    return {"ok": True, "ceiling": core.relationship_ceiling}


@router.get("/session/usage")
def get_session_usage(user_id: str = Depends(get_current_user_id)):
    from anjo.dashboard.session_store import get_session_snapshot
    from anjo.core.credits import cost_usd
    from anjo.core.llm import MODEL
    snapshot = get_session_snapshot(user_id)
    tokens = snapshot["state"].get("session_tokens", {"input": 0, "output": 0}) if snapshot else {"input": 0, "output": 0}
    cost = cost_usd(MODEL, tokens["input"], tokens["output"])
    return {
        "input_tokens": tokens["input"],
        "output_tokens": tokens["output"],
        "cost_usd": round(cost, 5),
    }
