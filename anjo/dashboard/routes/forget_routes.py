"""Account management: settings, forgetting, and deletion."""
from __future__ import annotations

import asyncio
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Request

_USERNAME_RE = re.compile(r'^[a-zA-Z0-9_-]+$')

from anjo.dashboard.auth import (
    COOKIE_NAME,
    change_password,
    delete_account,
    get_current_user_id,
    get_user_info,
    update_email,
    update_username,
    validate_password_strength,
    verify_password,
)

router = APIRouter()


# ── Account info ──────────────────────────────────────────────────────────────

@router.get("/account")
def account_info(user_id: str = Depends(get_current_user_id)):
    info = get_user_info(user_id)
    if not info:
        raise HTTPException(404, "User not found")
    return info


@router.post("/account/update-email")
async def account_update_email(request: Request, user_id: str = Depends(get_current_user_id)):
    body = await request.json()
    password = body.get("password", "")
    new_email = body.get("email", "").strip()
    if not verify_password(user_id, password):
        raise HTTPException(403, "Incorrect password.")
    if new_email and "@" not in new_email:
        raise HTTPException(400, "Invalid email address.")
    result, new_token = update_email(user_id, new_email)
    if result == "taken":
        raise HTTPException(409, "That email is already registered to another account.")
    # Send re-verification email so the user isn't permanently locked out
    if result and new_email and new_token and os.environ.get("RESEND_API_KEY"):
        info = get_user_info(user_id)
        username = info["username"] if info else user_id
        from anjo.core.email import send_verification_email
        await asyncio.to_thread(send_verification_email, new_email, username, new_token)
    return {"ok": True, "verification_sent": bool(result and new_email and new_token and os.environ.get("RESEND_API_KEY"))}


@router.post("/account/update-username")
async def account_update_username(request: Request, user_id: str = Depends(get_current_user_id)):
    body = await request.json()
    password = body.get("password", "")
    new_username = body.get("username", "").strip()
    if not verify_password(user_id, password):
        raise HTTPException(403, "Incorrect password.")
    if len(new_username) < 2:
        raise HTTPException(400, "Username must be at least 2 characters.")
    if len(new_username) > 32 or not _USERNAME_RE.match(new_username):
        raise HTTPException(400, "Username must be 2–32 characters (letters, numbers, _ or -).")
    ok = update_username(user_id, new_username)
    if not ok:
        raise HTTPException(409, "Username already taken.")
    return {"ok": True}


@router.post("/account/change-password")
async def account_change_password(request: Request, user_id: str = Depends(get_current_user_id)):
    body = await request.json()
    current = body.get("current_password", "")
    new_pw  = body.get("new_password", "")
    if not verify_password(user_id, current):
        raise HTTPException(403, "Incorrect current password.")
    pw_err = validate_password_strength(new_pw)
    if pw_err:
        raise HTTPException(400, pw_err)
    change_password(user_id, new_pw)
    return {"ok": True}


# ── Forgetting & deletion ─────────────────────────────────────────────────────

@router.post("/forget")
async def request_forget(request: Request, user_id: str = Depends(get_current_user_id)):
    body = await request.json()
    password = body.get("password", "")
    if not verify_password(user_id, password):
        raise HTTPException(403, "Incorrect password.")
    from anjo.core.forgetting import negotiate_and_forget
    response = negotiate_and_forget(user_id)
    return {"response": response}


@router.post("/account/delete")
async def account_delete(request: Request, user_id: str = Depends(get_current_user_id)):
    body = await request.json()
    password = body.get("password", "")
    if not verify_password(user_id, password):
        raise HTTPException(403, "Incorrect password.")
    await asyncio.to_thread(delete_account, user_id)
    from fastapi.responses import JSONResponse
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME)
    return resp
