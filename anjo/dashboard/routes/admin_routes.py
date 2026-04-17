"""Admin dashboard — protected by ANJO_ADMIN_SECRET env var."""
from __future__ import annotations

import hmac
import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from anjo.core.logger import logger

router = APIRouter()

_STATIC    = Path(__file__).parent.parent / "static"
_DATA_ROOT = Path(__file__).parent.parent.parent.parent / "data"

_USER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

def _validate_user_id(uid: str) -> bool:
    return bool(_USER_ID_RE.match(uid))


def _get_admin_secret() -> str:
    return os.environ.get("ANJO_ADMIN_SECRET", "")


def _authorized(request: Request) -> bool:
    secret = _get_admin_secret()
    if not secret:
        return False
    key = request.headers.get("X-Admin-Key", "")
    return hmac.compare_digest(key, secret)


def _unauth():
    return JSONResponse({"detail": "Unauthorized"}, status_code=401)


def require_admin(request: Request) -> None:
    """FastAPI dependency that enforces admin authentication.

    Usage:
        @router.get("/api/admin/users")
        def admin_users(request: Request, _: None = Depends(require_admin)):
            ...

    This ensures new admin routes are protected by default.
    """
    if not _authorized(request):
        raise HTTPException(status_code=401, detail="Admin authorization required")


_ADMIN_COOKIE = "anjo_admin_session"


@router.get("/admin")
async def admin_page(request: Request, key: str = ""):
    secret = _get_admin_secret()
    if not secret:
        from fastapi.responses import HTMLResponse
        return HTMLResponse("<h1>401 Unauthorized</h1><p>Admin secret not configured.</p>", status_code=401)

    # If key is provided via query param, authenticate, set cookie, and redirect
    # to clean URL (removes secret from browser history / logs)
    if key and hmac.compare_digest(key, secret):
        from fastapi.responses import RedirectResponse
        response = RedirectResponse("/admin", status_code=302)
        _secure = os.environ.get("ANJO_ENV") != "dev"
        # Cookie value is an HMAC of the secret — proves possession without storing the secret
        cookie_val = hmac.new(secret.encode(), b"anjo-admin-session", "sha256").hexdigest()
        response.set_cookie(
            _ADMIN_COOKIE, cookie_val,
            httponly=True, samesite="lax", secure=_secure, max_age=86400,
        )
        return response

    # Check cookie for subsequent visits
    cookie = request.cookies.get(_ADMIN_COOKIE, "")
    expected = hmac.new(secret.encode(), b"anjo-admin-session", "sha256").hexdigest()
    if cookie and hmac.compare_digest(cookie, expected):
        return FileResponse(_STATIC / "admin.html")

    from fastapi.responses import HTMLResponse
    return HTMLResponse(
        "<h1>401 Unauthorized</h1><p>Valid admin key required as ?key= query parameter.</p>",
        status_code=401,
    )


# ── Users list ────────────────────────────────────────────────────────────────

@router.get("/api/admin/users")
async def admin_users(request: Request, page: int = 1, limit: int = 100):
    if not _authorized(request):
        return _unauth()
    page  = max(1, page)
    limit = max(1, min(limit, 500))

    from anjo.dashboard.auth import list_users
    from anjo.core.subscription import get_tier, get_daily_messages_used, get_daily_limit
    from anjo.core.credits import get_balance, get_message_credits
    from anjo.dashboard.session_store import get_active_session_count, get_session_status

    rows: list[dict] = []

    def _safe(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    for u in list_users():
        uid      = u.get("user_id", "")
        username = u.get("username", "")
        user_dir = _DATA_ROOT / "users" / uid

        size_bytes = 0
        if user_dir.exists():
            size_bytes = sum(
                f.stat().st_size for f in user_dir.rglob("*") if f.is_file()
            )

        is_active, last_activity = get_session_status(uid)

        # Count chat messages
        chat_file = user_dir / "chat_history.jsonl"
        chat_count = 0
        if chat_file.exists():
            try:
                chat_count = sum(1 for line in chat_file.read_text().splitlines() if line.strip())
            except Exception:
                pass

        rows.append({
            "user_id":         uid,
            "username":        username,
            "email":           u.get("email", ""),
            "email_verified":  bool(u.get("email_verified", False)),
            "created_at":      u.get("created_at", ""),
            "tier":            _safe(lambda uid=uid: get_tier(uid), "free"),
            "balance_usd":     round(_safe(lambda uid=uid: get_balance(uid), 0.0), 4),
            "message_credits": _safe(lambda uid=uid: get_message_credits(uid), 0),
            "daily_used":      _safe(lambda uid=uid: get_daily_messages_used(uid), 0),
            "daily_limit":     _safe(lambda uid=uid: get_daily_limit(uid), 20),
            "has_self_core":   (user_dir / "self_core" / "current.json").exists(),
            "has_memories":    (user_dir / "memories").exists(),
            "data_size_kb":    round(size_bytes / 1024, 1),
            "is_active":       is_active,
            "last_activity":   last_activity,
            "chat_count":      chat_count,
        })

    rows.sort(key=lambda r: r["created_at"], reverse=True)

    total = len(rows)
    start = (page - 1) * limit
    paged = rows[start : start + limit]

    return {
        "users":           paged,
        "total":           total,
        "page":            page,
        "pages":           max(1, -(-total // limit)),
        "active_sessions": get_active_session_count(),
        "subscribers":     sum(1 for r in rows if r["tier"] != "free"),
        "total_balance":   round(sum(r["balance_usd"] for r in rows), 2),
    }


# ── Per-user actions ──────────────────────────────────────────────────────────

@router.post("/api/admin/users/{user_id}/verify")
async def admin_verify(user_id: str, request: Request):
    if not _authorized(request):
        return _unauth()
    if not _validate_user_id(user_id):
        return JSONResponse({"detail": "Invalid user_id format"}, status_code=400)
    from anjo.core.db import get_db
    db = get_db()
    db.execute(
        "UPDATE users SET email_verified = 1, verification_token = '', verification_token_hmac = '' "
        "WHERE user_id = ?",
        (user_id,),
    )
    db.commit()
    row = db.execute("SELECT changes()").fetchone()
    changed = row[0] if row is not None else 0
    if not changed:
        return JSONResponse({"detail": "User not found"}, status_code=404)
    return {"ok": True}


@router.post("/api/admin/users/{user_id}/credits")
async def admin_add_credits(user_id: str, request: Request, amount: float = 5.0):
    if not _authorized(request):
        return _unauth()
    if not _validate_user_id(user_id):
        return JSONResponse({"detail": "Invalid user_id format"}, status_code=400)
    if amount <= 0 or amount > 1000:
        return JSONResponse({"detail": "Amount must be 0–1000"}, status_code=400)
    from anjo.core.credits import add_credits
    new_balance = add_credits(user_id, amount)
    return {"ok": True, "balance_usd": new_balance}


@router.post("/api/admin/users/{user_id}/tier")
async def admin_set_tier(user_id: str, request: Request, tier: str = "free"):
    if not _authorized(request):
        return _unauth()
    if not _validate_user_id(user_id):
        return JSONResponse({"detail": "Invalid user_id format"}, status_code=400)
    if tier not in ("free", "pro", "premium"):
        return JSONResponse({"detail": "Invalid tier"}, status_code=400)
    from anjo.core.subscription import set_subscription
    if tier == "free":
        from anjo.core.db import get_db
        get_db().execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        get_db().commit()
    else:
        set_subscription(user_id, status="active", tier=tier)
    return {"ok": True, "tier": tier}


@router.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request):
    if not _authorized(request):
        return _unauth()
    if not _validate_user_id(user_id):
        return JSONResponse({"detail": "Invalid user_id format"}, status_code=400)
    from anjo.dashboard.auth import delete_account
    from anjo.dashboard.session_store import delete_session
    delete_session(user_id)
    delete_account(user_id)
    return {"ok": True}


@router.post("/api/admin/users/{user_id}/reset")
async def admin_reset_user(user_id: str, request: Request):
    """Wipe all memory/personality data but keep account."""
    if not _authorized(request):
        return _unauth()
    if not _validate_user_id(user_id):
        return JSONResponse({"detail": "Invalid user_id format"}, status_code=400)

    # 1. ChromaDB — delete only this user's vectors, not the entire shared collection
    try:
        from anjo.memory.long_term import _get_collections
        semantic_col, emotional_col = _get_collections()
        for col in (semantic_col, emotional_col):
            try:
                ids = col.get(where={"user_id": user_id}, include=[])["ids"]
                if ids:
                    col.delete(ids=ids)
            except Exception as e:
                logger.warning(f"Could not delete vectors for {user_id} from {col.name}: {e}")
    except Exception as e:
        logger.warning(f"ChromaDB reset failed for {user_id}: {e}")

    # 2. Self-core
    try:
        from anjo.core.self_core import _core_dir
        core_dir = _core_dir(user_id)
        current_path = core_dir / "current.json"
        history_dir  = core_dir / "history"
        if current_path.exists():
            current_path.unlink()
        if history_dir.exists():
            for f in history_dir.iterdir():
                if f.is_file():
                    f.unlink()
    except Exception as e:
        logger.warning(f"Self-core reset failed for {user_id}: {e}")

    # 3. Chat history
    try:
        from anjo.core.history import clear as clear_history
        clear_history(user_id)
    except Exception as e:
        logger.warning(f"Chat history reset failed for {user_id}: {e}")

    # 4. Reflection log
    try:
        from anjo.reflection.log import _log_path
        lp = _log_path(user_id)
        if lp.exists():
            lp.unlink()
    except Exception as e:
        logger.warning(f"Reflection log reset failed for {user_id}: {e}")

    # 5. Letter cache
    try:
        from anjo.core.db import get_db
        get_db().execute("DELETE FROM letter_cache WHERE user_id = ?", (user_id,))
        get_db().execute("DELETE FROM facts WHERE user_id = ?", (user_id,))
        get_db().commit()
    except Exception as e:
        logger.warning(f"DB reset failed for {user_id}: {e}")

    # 6. Evict session
    try:
        from anjo.dashboard.session_store import delete_session, get_or_create_session
        delete_session(user_id)
        get_or_create_session(user_id)
    except Exception as e:
        logger.warning(f"Session reset failed for {user_id}: {e}")

    return {"ok": True}


@router.get("/api/admin/users/{user_id}/chat")
async def admin_chat_history(user_id: str, request: Request, n: int = 50):
    """Return chat history metadata. Content is redacted by default (privacy policy).

    Pass ?include_content=true to see full messages (audit-logged).
    """
    if not _authorized(request):
        return _unauth()
    if not _validate_user_id(user_id):
        return JSONResponse({"detail": "Invalid user_id format"}, status_code=400)
    n = max(1, min(n, 500))
    include_content = request.query_params.get("include_content", "").lower() == "true"
    if include_content:
        logger.warning(f"Admin accessed full chat content for user {user_id}")
    try:
        from anjo.core.history import get_history
        messages = get_history(user_id, limit=n)
    except Exception as e:
        logger.warning(f"Could not read chat history for {user_id}: {e}")
        messages = []
    if not include_content:
        # Redact message content — return only metadata (role, timestamp, length)
        messages = [
            {k: (v if k != "content" else f"[redacted — {len(v)} chars]") for k, v in m.items()}
            if isinstance(m, dict) and "content" in m else m
            for m in messages
        ]
    return {"user_id": user_id, "messages": messages, "total": len(messages), "content_included": include_content}


@router.get("/api/admin/users/{user_id}/self-core")
async def admin_self_core(user_id: str, request: Request):
    if not _authorized(request):
        return _unauth()
    if not _validate_user_id(user_id):
        return JSONResponse({"detail": "Invalid user_id format"}, status_code=400)
    core_file = _DATA_ROOT / "users" / user_id / "self_core" / "current.json"
    if not core_file.exists():
        return {"user_id": user_id, "data": None}
    try:
        from anjo.core.crypto import read_encrypted
        data = json.loads(read_encrypted(core_file))
    except Exception:
        return {"user_id": user_id, "data": None}
    return {"user_id": user_id, "data": data}


@router.get("/api/admin/users/{user_id}/profile")
async def admin_profile(user_id: str, request: Request):
    """Rich visual profile — personality, facts, mood, relationship, recent reflections."""
    if not _authorized(request):
        return _unauth()
    if not _validate_user_id(user_id):
        return JSONResponse({"detail": "Invalid user_id format"}, status_code=400)

    from anjo.core.crypto import read_encrypted

    # Self-core
    core_data = None
    core_file = _DATA_ROOT / "users" / user_id / "self_core" / "current.json"
    if core_file.exists():
        try:
            core_data = json.loads(read_encrypted(core_file))
        except Exception:
            pass

    # Facts
    facts = []
    try:
        from anjo.core.facts import load_facts
        facts = load_facts(user_id)
    except Exception:
        pass

    # Journal snippet (first 20 lines)
    journal_snippet = None
    journal_file = _DATA_ROOT / "users" / user_id / "journal.md"
    if journal_file.exists():
        try:
            text = read_encrypted(journal_file)
            journal_snippet = "\n".join(text.splitlines()[:20])
        except Exception:
            pass

    # Reflection log (last 5)
    reflections = []
    try:
        from anjo.reflection.log import read_log
        reflections = read_log(user_id, limit=5)
    except Exception:
        pass

    return {
        "user_id":    user_id,
        "core":       core_data,
        "facts":      facts,
        "journal":    journal_snippet,
        "reflections": reflections,
    }
