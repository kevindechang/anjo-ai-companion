"""Mobile auth endpoints — JSON login/register that return Bearer tokens."""

from __future__ import annotations

import asyncio
import os
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from anjo.dashboard.auth import (
    authenticate_user,
    force_verify_email,
    is_email_verified,
    make_token,
    register_user,
    validate_password_strength,
)

router = APIRouter()

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str


@router.post("/login")
async def mobile_login(body: LoginRequest):
    """Authenticate and return a Bearer token for mobile clients.
    Supports case-insensitive username or email login.
    """
    user_id = authenticate_user(body.username, body.password)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    email_service_configured = bool(os.environ.get("RESEND_API_KEY", ""))
    if email_service_configured and not is_email_verified(user_id):
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password",
        )
    return {"token": make_token(user_id), "user_id": user_id}


@router.post("/register")
async def mobile_register(body: RegisterRequest):
    """Create a new account and return a Bearer token for mobile clients."""
    pw_err = validate_password_strength(body.password)
    if (
        len(body.username) < 2
        or len(body.username) > 32
        or not _USERNAME_RE.match(body.username)
        or pw_err
        or not body.email.strip()
        or "@" not in body.email
    ):
        raise HTTPException(
            status_code=400,
            detail=pw_err
            or "Valid email required, username 2–32 chars (letters, numbers, _ or -), password ≥ 8 chars with at least one number or symbol",
        )
    user, err = register_user(body.username, body.password, body.email)
    if not user:
        raise HTTPException(
            status_code=409, detail="An account with that username or email already exists"
        )

    email = body.email.strip()
    if email and not user.get("email_verified"):
        from anjo.core.email import send_verification_email

        sent = await asyncio.to_thread(
            send_verification_email, email, body.username, user["verification_token"]
        )
        if sent:
            return {"message": "Check your email to verify your account."}
        # Email service unavailable — auto-verify so user isn't locked out
        force_verify_email(body.username)

    # Email service unavailable — grant credits and return token
    from anjo.core.credits import grant_initial_credits

    grant_initial_credits(user["user_id"])
    token = make_token(user["user_id"])
    return {"token": token, "user_id": user["user_id"]}
