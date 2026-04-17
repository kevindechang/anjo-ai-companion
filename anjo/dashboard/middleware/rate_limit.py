"""Rate limiting middleware for the Anjo dashboard.

Sliding-window in-memory rate limiter (single process). Stores hit timestamps
per key and prunes on each check. Good enough until multi-worker deployment.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from anjo.dashboard.auth import _token_from_request, verify_token


_rl_lock = threading.Lock()
_rl_hits: dict[str, list[float]] = defaultdict(list)

# (path_prefix, window_seconds, max_hits)
_RL_RULES = [
    ("/api/chat",    60, 30),   # chat stream — 30/min per user (scripts stopped here)
    ("/api/billing", 60, 20),   # billing — 20/min per user
    ("/api/auth",    60, 10),   # auth — 10/min per IP (brute force)
    ("/api/",        60, 120),  # all other API — 120/min per user
]

_WEB_AUTH_PATHS = {"/login", "/forgot", "/reset", "/register", "/admin"}

_WEB_AUTH_RL_WINDOW = 60   # seconds
_WEB_AUTH_RL_MAX    = 10   # requests per window


def _rl_key(request: Request) -> str:
    """Rate limit key: user_id for authenticated routes, IP for auth routes.

    IMPORTANT: Requires reverse proxy (nginx/caddy) to set X-Real-IP header.
    Without a proxy, X-Forwarded-For can be spoofed by clients.
    """
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        ip = real_ip
    else:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ips = [x.strip() for x in forwarded.split(",") if x.strip()]
            ip = ips[-1] if ips else (request.client.host if request.client else "unknown")
        else:
            ip = request.client.host if request.client else "unknown"

    path = request.url.path
    if path.startswith("/api/auth"):
        return f"ip:{ip}"
    token = _token_from_request(request)
    uid   = verify_token(token) if token else None
    return f"u:{uid}" if uid else f"ip:{ip}"


def _check_rate_limit(key: str, path: str) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds)."""
    rule = next(
        ((w, m) for prefix, w, m in _RL_RULES if path.startswith(prefix)),
        (60, 120),
    )
    window, max_hits = rule
    now = time.monotonic()
    with _rl_lock:
        hits = _rl_hits[key]
        cutoff = now - window
        while hits and hits[0] < cutoff:
            hits.pop(0)
        if len(hits) >= max_hits:
            retry = int(window - (now - hits[0])) + 1
            return False, retry
        hits.append(now)
        return True, 0


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _WEB_AUTH_PATHS:
            # IP-based rate limiting for web auth forms (10 req/min)
            real_ip = request.headers.get("X-Real-IP")
            if real_ip:
                ip = real_ip
            else:
                forwarded = request.headers.get("X-Forwarded-For")
                if forwarded:
                    ips = [x.strip() for x in forwarded.split(",") if x.strip()]
                    ip = ips[-1] if ips else (request.client.host if request.client else "unknown")
                else:
                    ip = request.client.host if request.client else "unknown"
            key = f"ip:{ip}"
            now = time.monotonic()
            with _rl_lock:
                hits = _rl_hits[key]
                cutoff = now - _WEB_AUTH_RL_WINDOW
                while hits and hits[0] < cutoff:
                    hits.pop(0)
                if len(hits) >= _WEB_AUTH_RL_MAX:
                    retry = int(_WEB_AUTH_RL_WINDOW - (now - hits[0])) + 1
                    return HTMLResponse(
                        "<h1>429 Too Many Requests</h1><p>Please slow down and try again later.</p>",
                        status_code=429,
                        headers={"Retry-After": str(retry)},
                    )
                hits.append(now)
            return await call_next(request)
        # Only rate-limit API routes beyond this point
        if not path.startswith("/api/"):
            return await call_next(request)
        # Provider webhooks must not be throttled by shared provider egress IPs
        if path in ("/api/billing/webhook", "/api/billing/webhook/revenuecat"):
            return await call_next(request)
        key = _rl_key(request)
        allowed, retry = _check_rate_limit(key, path)
        if not allowed:
            return JSONResponse(
                {"detail": "Too many requests. Please slow down."},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        return await call_next(request)
