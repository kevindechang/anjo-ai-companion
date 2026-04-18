"""Login / register / logout routes."""

from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from anjo.dashboard.auth import (
    COOKIE_NAME,
    authenticate_user,
    consume_reset_token,
    force_verify_email,
    generate_reset_token,
    is_email_verified,
    make_token,
    register_user,
    revoke_token,
    validate_password_strength,
    validate_reset_token,
    verify_email_token,
    verify_token,
)

router = APIRouter()

_STATIC = Path(__file__).parent.parent / "static"


def _read(name: str) -> str:
    return (_STATIC / name).read_text()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if verify_token(request.cookies.get(COOKIE_NAME, "")):
        return RedirectResponse("/chat", status_code=302)
    return HTMLResponse(_read("login.html"))


@router.post("/login")
async def login_submit(username: str = Form(...), password: str = Form(...)):
    # username field now supports email too
    user_id = authenticate_user(username, password)
    if not user_id:
        html = _read("login.html").replace(
            "<!--ERROR-->",
            '<p style="color:var(--red);text-align:center;margin-top:12px;">Invalid username or password.</p>',
        )
        return HTMLResponse(html, status_code=401)

    import os

    email_service_configured = bool(os.environ.get("RESEND_API_KEY", ""))
    if email_service_configured and not is_email_verified(user_id):
        html = _read("login.html").replace(
            "<!--ERROR-->",
            '<p style="color:var(--red);text-align:center;margin-top:12px;">Invalid username or password.</p>',
        )
        return HTMLResponse(html, status_code=401)

    response = RedirectResponse("/chat", status_code=302)
    _secure = os.environ.get("ANJO_ENV") != "dev"
    response.set_cookie(
        COOKIE_NAME, make_token(user_id), httponly=True, samesite="lax", secure=_secure
    )
    return response


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    if verify_token(request.cookies.get(COOKIE_NAME, "")):
        return RedirectResponse("/chat", status_code=302)
    return HTMLResponse(_read("register.html"))


@router.post("/register")
async def register_submit(
    username: str = Form(...), password: str = Form(...), email: str = Form(...)
):
    _USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
    pw_err = validate_password_strength(password)
    if (
        len(username) < 2
        or len(username) > 32
        or not _USERNAME_RE.match(username)
        or pw_err
        or not email.strip()
        or "@" not in email
    ):
        detail = (
            pw_err
            or "Valid email required, username 2–32 chars (letters, numbers, _ or -), password ≥ 8 chars with at least one number or symbol."
        )
        html = _read("register.html").replace(
            "<!--ERROR-->",
            f'<p style="color:var(--red);text-align:center;margin-top:12px;">{detail}</p>',
        )
        return HTMLResponse(html, status_code=400)
    user, err = register_user(username, password, email)
    if not user:
        html = _read("register.html").replace(
            "<!--ERROR-->",
            '<p style="color:var(--red);text-align:center;margin-top:12px;">An account with that username or email already exists.</p>',
        )
        return HTMLResponse(html, status_code=409)

    if email.strip() and not user.get("email_verified"):
        # Attempt to send verification email — credits granted after verification
        import asyncio

        from anjo.core.email import send_verification_email

        sent = await asyncio.to_thread(
            send_verification_email, email.strip(), username, user["verification_token"]
        )
        if sent:
            html = _read("register.html").replace(
                "<!--ERROR-->",
                '<p style="color:#c9a96e;text-align:center;margin-top:12px;">Check your email to verify your account.</p>',
            )
            return HTMLResponse(html)
        # Email service unavailable — auto-verify and log them in so they're not locked out
        force_verify_email(username)

    # No email provided, or email service unavailable — grant credits and log in directly
    from anjo.core.credits import grant_initial_credits

    grant_initial_credits(user["user_id"])
    response = RedirectResponse("/chat", status_code=302)
    _secure = os.environ.get("ANJO_ENV") != "dev"
    response.set_cookie(
        COOKIE_NAME, make_token(user["user_id"]), httponly=True, samesite="lax", secure=_secure
    )
    return response


@router.get("/verify")
async def verify_email(token: str = ""):
    user_id = verify_email_token(token)
    if not user_id:
        return HTMLResponse(
            '<p style="font-family:system-ui;text-align:center;margin-top:40px;">Invalid or expired link.</p>',
            status_code=400,
        )
    from anjo.core.credits import grant_initial_credits

    grant_initial_credits(user_id)
    return RedirectResponse("/login?verified=1", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    revoke_token(request.cookies.get(COOKIE_NAME, ""))
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response


# ── Password reset ────────────────────────────────────────────────────────────


@router.get("/forgot", response_class=HTMLResponse)
async def forgot_page():
    return HTMLResponse(_read("forgot.html"))


@router.post("/forgot", response_class=HTMLResponse)
async def forgot_submit(email: str = Form(...)):
    result = generate_reset_token(email.strip())
    # Always show success — don't reveal whether email exists
    if result:
        import asyncio

        username, token = result
        from anjo.core.email import send_reset_email

        await asyncio.to_thread(send_reset_email, email.strip(), username, token)
    html = _read("forgot.html").replace(
        "<!--MSG-->",
        '<p style="color:#c9a96e;text-align:center;margin-top:12px;">If that email is registered you\'ll receive a reset link shortly.</p>',
    )
    return HTMLResponse(html)


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


@router.get("/reset", response_class=HTMLResponse)
async def reset_page(token: str = ""):
    username = validate_reset_token(token)
    if not username:
        return HTMLResponse(
            '<p style="font-family:system-ui;text-align:center;margin-top:40px;color:#c97070;">This reset link is invalid or has expired. <a href="/forgot">Request a new one.</a></p>',
            status_code=400,
        )
    html = _read("reset.html").replace("<!--TOKEN-->", _html_escape(token))
    return HTMLResponse(html)


@router.post("/reset", response_class=HTMLResponse)
async def reset_submit(token: str = Form(...), password: str = Form(...)):
    pw_err = validate_password_strength(password)
    if pw_err:
        html = (
            _read("reset.html")
            .replace("<!--TOKEN-->", _html_escape(token))
            .replace(
                "<!--ERROR-->",
                f'<p style="color:var(--red);text-align:center;margin-top:12px;">{pw_err}</p>',
            )
        )
        return HTMLResponse(html, status_code=400)
    ok = consume_reset_token(token, password)
    if not ok:
        return HTMLResponse(
            '<p style="font-family:system-ui;text-align:center;margin-top:40px;color:#c97070;">Link expired. <a href="/forgot">Request a new one.</a></p>',
            status_code=400,
        )
    return RedirectResponse("/login?reset=1", status_code=302)
