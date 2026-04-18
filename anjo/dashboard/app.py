"""Anjo Dashboard — FastAPI application."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load .env before any anjo.* imports — crypto.py derives keys from ANJO_SECRET
# at first use, which happens at import time via module-level constants.
load_dotenv()

from anjo.core.logger import logger
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from anjo.dashboard.auth import (
    COOKIE_NAME,
    _token_from_request,
    has_any_users,
    should_skip_auth,
    verify_token,
)
from anjo.dashboard.middleware.rate_limit import RateLimitMiddleware
from anjo.dashboard.watchers import _inactivity_watcher, _drift_watcher

from anjo.dashboard.routes.self_core_routes import router as self_core_router
from anjo.dashboard.routes.memory_routes import router as memory_router
from anjo.dashboard.routes.chat_routes import router as chat_router
from anjo.dashboard.routes.reset_routes import router as reset_router
from anjo.dashboard.routes.auth_routes import router as auth_router
from anjo.dashboard.routes.story_routes import router as story_router
from anjo.dashboard.routes.billing_routes import router as billing_router
from anjo.dashboard.routes.forget_routes import router as forget_router
from anjo.dashboard.routes.admin_routes import router as admin_router


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        # Block direct static file access to admin.html — force through /admin (which requires ?key=)
        if request.url.path == "/static/admin.html":
            return RedirectResponse("/admin", status_code=302)
        if should_skip_auth(request.url.path):
            return await call_next(request)
        user_id = verify_token(_token_from_request(request))
        if user_id:
            return await call_next(request)
        # API routes: return 401 JSON (mobile clients don't follow redirects)
        if request.url.path.startswith("/api"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        # Web routes: redirect to login or first-time setup
        if not has_any_users():
            return RedirectResponse("/register", status_code=302)
        return RedirectResponse("/login", status_code=302)


@asynccontextmanager
async def lifespan(_: FastAPI):
    from anjo.core.transcript_queue import process_all_pending
    from anjo.dashboard.session_store import recover_sessions_on_startup

    # Verify encryption key is properly configured before serving
    from anjo.core.crypto import verify_production_key
    verify_production_key()

    # Recover sessions from SQLite — sessions now survive server restarts
    recovered = recover_sessions_on_startup()
    if recovered:
        logger.info(f"Recovered {recovered} active session(s) from database")

    # Load revoked tokens from DB so logout survives restarts
    from anjo.dashboard.auth import load_revoked_tokens_from_db
    load_revoked_tokens_from_db()

    count = process_all_pending()
    if count:
        logger.info(f"Caught up on {count} pending transcript(s)")

    inactivity_task = asyncio.create_task(_inactivity_watcher())
    drift_task      = asyncio.create_task(_drift_watcher())
    yield
    inactivity_task.cancel()
    drift_task.cancel()


app = FastAPI(title="Anjo Dashboard", docs_url=None, redoc_url=None, lifespan=lifespan)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://d1f8f9xcsvx3ha.cloudfront.net; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-src 'none'; "
            "frame-ancestors 'none';"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        if os.environ.get("ANJO_ENV") != "dev":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


# CORS origins - dev allows all for local mobile testing
cors_origins = (
    ["*"]  # Allow all origins in dev mode for mobile testing
    if os.environ.get("ANJO_ENV") == "dev"
    else [
        os.environ.get("ANJO_BASE_URL", "https://your-domain.com"),
        "http://localhost:8081",  # Expo dev
        "http://localhost:19000", # Expo dev
        "http://localhost:19001", # Expo dev
        "http://localhost:8000",  # Web dev
    ]
)

app.add_middleware(AuthMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)

from anjo.dashboard.routes.mobile_auth_routes import router as mobile_auth_router
app.include_router(admin_router)
app.include_router(mobile_auth_router, prefix="/api/auth")
app.include_router(auth_router)
app.include_router(self_core_router, prefix="/api")
app.include_router(memory_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(reset_router, prefix="/api")
app.include_router(story_router, prefix="/api")
app.include_router(billing_router, prefix="/api")
app.include_router(forget_router, prefix="/api")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/sw.js")
async def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


@app.get("/static/admin.html")
async def block_admin_static():
    """Block direct StaticFiles access to admin.html — force through /admin which requires ?key=."""
    return RedirectResponse("/admin", status_code=302)


@app.get("/")
async def landing():
    return FileResponse(
        STATIC_DIR / "landing.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/chat")
async def chat():
    return FileResponse(STATIC_DIR / "chat.html")


@app.get("/story")
async def story():
    return FileResponse(STATIC_DIR / "story.html")


@app.get("/privacy")
async def privacy():
    return FileResponse(STATIC_DIR / "privacy.html")


@app.get("/terms")
async def terms():
    return FileResponse(STATIC_DIR / "terms.html")


@app.get("/refund")
async def refund():
    return FileResponse(STATIC_DIR / "refund.html")


@app.get("/billing")
async def billing():
    return FileResponse(STATIC_DIR / "billing.html")


@app.get("/dev")
async def dev(request: Request):
    return RedirectResponse("/chat", status_code=302)


@app.get("/debug")
async def debug(request: Request):
    # Internal debug dashboard — dev environment or admin only
    if os.environ.get("ANJO_ENV") == "dev":
        return FileResponse(STATIC_DIR / "index.html")
    token = request.cookies.get(COOKIE_NAME, "")
    user_id = verify_token(token)
    if user_id == os.environ.get("ANJO_ADMIN_USER_ID", ""):
        return FileResponse(STATIC_DIR / "index.html")
    return RedirectResponse("/chat", status_code=302)


def run() -> None:
    import uvicorn
    dev = os.environ.get("ANJO_ENV", "dev") == "dev"
    uvicorn.run(
        "anjo.dashboard.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=dev,
    )
