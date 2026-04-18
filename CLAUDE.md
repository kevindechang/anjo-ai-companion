# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## About This Repository

This is the **Anjo Scaffold** — a production-ready starter for building AI companion apps.
It provides the infrastructure, auth, memory, and LangGraph conversation pipeline.
The core AI personality logic (system prompt, reflection engine) has intentional stubs
that you implement to define your own companion's character.

See `README.md` for the quickstart guide and `docs/` for architecture documentation.

---

## Commands

```bash
# Install (editable)
pip install -e ".[test]"

# Run the dashboard server (dev mode — auto-reload, localhost only)
ANJO_ENV=dev anjo-dashboard
# or directly:
ANJO_ENV=dev uvicorn anjo.dashboard.app:app --reload --port 8000

# CLI chat REPL (talks to local Ollama or dev env)
anjo chat --user my_user_id

# Run all tests
pytest

# Run a single test file
pytest tests/test_auth.py -v

# Run a specific test
pytest tests/test_auth.py::TestLogin::test_login_success -v
```

**Required env vars** (copy `.env.example` → `.env`):
- `ANTHROPIC_API_KEY` — Claude API
- `ANJO_SECRET` — HMAC signing secret for session tokens (min 32 random bytes in prod)
- `ANJO_ADMIN_SECRET` — Admin panel key (must be strong in prod; rotate to invalidate)
- `ANJO_BASE_URL` — e.g. `https://your-domain.com`
- `RESEND_API_KEY` — optional; if absent, email verification is skipped and users auto-verify
- `ANJO_ENV=dev` — skips HTTPS enforcement, allows localhost CORS, relaxes startup checks

**Test isolation**: `conftest.py` redirects all DB and file I/O to `tmp_path`, clears in-memory state, and removes real API keys so no external calls are made.

---

## Actual Tech Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI 0.115+ / Uvicorn (single worker) |
| Conversation orchestration | LangGraph (StateGraph, compiled singleton) |
| LLM | Anthropic Claude Sonnet (responses) + Haiku (background classification/facts) |
| Long-term memory | ChromaDB (local, unencrypted on disk) |
| Short-term memory | In-process session dict (`session_store.py`) |
| Personality embeddings | sentence-transformers `all-MiniLM-L6-v2` |
| Self-Core state | Per-user JSON files in `data/users/{user_id}/self_core/` |
| Database | SQLite in WAL mode (`data/anjo.db`) — per-thread connections |
| Mobile client | React Native / Expo ~54 (`mobile/` directory) |
| Email | Resend API |
| Billing | RevenueCat (`PAYMENTS_ENABLED=True`) |

---

## Architecture

### Request Flow (Web)

```
nginx → Uvicorn
  → SecurityHeadersMiddleware (CSP, HSTS, Referrer-Policy, etc.)
  → CORSMiddleware
  → RateLimitMiddleware (sliding window, in-memory)
  → AuthMiddleware (HMAC token verification + skip-list)
  → FastAPI routing → route handler
```

Middleware execution order is **reverse** of `add_middleware()` call order — the last `add_middleware()` call is outermost (first to see the request).

### Conversation Graph

**Live path** (chat_routes.py — streaming SSE):
```
perceive → gate_node ──► [retrieve?] → appraise → respond (streaming)
                     └──► silent (yields done event, no LLM call)
```

**Test/CLI path** (conversation_graph.py — compiled LangGraph singleton):
```
perceive → classify ──► retrieve → appraise → respond (non-streaming)
                    └──► appraise → respond
```

- **gate_node**: Single Haiku call replacing the old separate `classify_node + silence_node`. Classifies intent, decides whether to pull long-term memory, and decides whether Anjo should respond at all. On error, defaults to respond.
- **retrieve**: Fetches relevant semantic + emotional memory chunks from ChromaDB (conditional, ~20% of turns).
- **appraise**: OCC emotion appraisal + PAD mood update using classified intent.
- **respond (live)**: Builds system prompt from SelfCore + memories, streams Claude Sonnet response via SSE.

Per-conversation state lives in `session_store._sessions` (in-memory dict, lost on restart).

### Self-Core

`SelfCore` (`anjo/core/self_core.py`) is a Pydantic model representing the companion's live personality state. It's:
- Loaded from `data/users/{user_id}/self_core/current.json` at session start
- Injected into every system prompt via `prompt_builder.py`
- Updated by the **Reflection Engine** post-conversation

### Reflection Engine

Full reflection is triggered two ways:
1. **Explicit**: `POST /chat/{session_id}/end` — frontend calls this on session close
2. **Automatic**: `_inactivity_watcher()` background task (60s poll) detects sessions idle >10min and auto-reflects

Additionally, **mid-session mini-reflection** runs every 20 messages in a background thread via `_maybe_mid_reflect()`.

Reflection flow:
1. `run_reflection()` receives the transcript (seed messages excluded to prevent double-reflection) and current `SelfCore`
2. Your implementation analyzes the conversation and mutates SelfCore fields: OCEAN traits, attachment, desires, preoccupation, notes, relationship stage
3. Saves updated SelfCore to disk; clears the session from memory

### Memory: Dual Embeddings

Per session, two embeddings are stored in ChromaDB:
- **Semantic vector**: What happened (content summary)
- **Emotional vector**: How it felt (emotional metadata)

Both are scoped by `user_id` in metadata. Retrieval uses cosine similarity with an emotion-weighted re-ranking.

### Session Store

`session_store.py` holds an in-memory `_sessions: dict[str, dict]` — one entry per active user. Sessions contain the live `SelfCore`, conversation history, and token accumulators. **Lost on server restart.** Sessions are cleaned up after reflection completes.

---

## What You Need to Implement

The scaffold ships two intentional stubs — these are where you define your companion:

### 1. `anjo/core/prompt_builder.py` — `build_system_prompt()`

This function builds the system prompt sent to Claude on every turn. It receives:
- `core` — the companion's current SelfCore (personality, mood, relationship state)
- `retrieved_memories` — relevant memories from ChromaDB
- `active_emotions` — emotions appraised from the current turn

Return a `(static_block, dynamic_block)` tuple. Static block is prompt-cached; dynamic is rebuilt each turn.

### 2. `anjo/reflection/engine.py` — `run_reflection()`

This function runs after each session ends. It receives the full transcript and current SelfCore.
It should analyze the conversation and update the companion's personality/relationship state on disk.

Both files contain detailed docstrings with the expected interface.

---

## Auth & Security Model

### Two-Role System

**Users** — HMAC-SHA256 signed tokens (`user_id.iat.exp.sig`), 7-day TTL, delivered as `anjo_auth` HttpOnly cookie (web) or `Authorization: Bearer` header (mobile).

**Admin** — Static `ANJO_ADMIN_SECRET` env var, passed as `X-Admin-Key` header to all `/api/admin/*` endpoints. Completely independent from the user token system.

### Token Verification (`auth.py:verify_token`)

Checks in order (each short-circuits on failure):
1. Expiry (`exp > now`)
2. HMAC signature
3. In-memory revocation set (`_revoked_tokens`) — populated on logout
4. `password_changed_at` DB lookup — rejects tokens issued before last password change

### Auth Bypass List (`auth.py:should_skip_auth`)

`AuthMiddleware` skips token checking for public paths: `/`, `/login`, `/register`, `/logout`, `/forgot`, `/reset`, `/verify`, static files, `/admin`, and all `/api/admin/*`. Admin routes enforce their own `X-Admin-Key` check per-handler.

**`/static/admin.html`** is explicitly intercepted in `AuthMiddleware` and redirected to `/admin` — preventing direct StaticFiles bypass of the admin page key guard.

### Rate Limiting (`app.py:RateLimitMiddleware`)

Sliding window, in-memory, **reset on restart**:
- `/login`, `/forgot`, `/reset` — 10 req/min per IP
- `/api/auth/*` — 10 req/min per IP  
- `/api/chat/*` — 30 req/min per user
- `/api/billing/*` — 20 req/min per user
- `/api/*` (catch-all) — 120 req/min per user/IP

### Admin Panel

`GET /admin` requires `?key=ANJO_ADMIN_SECRET` query parameter (server-side validated with `hmac.compare_digest`). Without a valid key, returns 401. The admin key has no expiry mechanism — rotate `ANJO_ADMIN_SECRET` and restart to invalidate.

### Security Headers (all responses)

`Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`, `HSTS` (prod only).

### Known Structural Limitations

- **Token revocation is in-memory** — server restart clears it. Tokens issued before restart and before `password_changed_at` can be replayed until they expire (max 7 days). The `password_changed_at` DB check survives restarts.
- **Admin key has no expiry** — it's a static env var. Rotate `ANJO_ADMIN_SECRET` and restart to invalidate if it leaks.
- **Mobile `AsyncStorage`** stores tokens unencrypted — a React Native app concern, not backend.
- **ChromaDB on disk is unencrypted** — acceptable for current threat model.

---

## Database Schema (`anjo/core/db.py`)

SQLite with WAL mode, per-thread connections via `threading.local()`. Schema initializes once per process (`_schema_initialized` flag + `_init_lock`). Additive migrations run via `_migrate_schema()` on first connection.

Key tables: `users`, `messages`, `credits`, `subscriptions`, `daily_usage`, `facts`, `letter_cache`, `processed_transactions`.

`users` table security columns: `hashed_password` (bcrypt factor 12), `reset_token` (UUID4, 1hr TTL), `verification_token`, `password_changed_at` (ISO timestamp, set on any password change/reset).

---

## Route Organization

All routes are in `anjo/dashboard/routes/`:

| File | Prefix | Responsibility |
|---|---|---|
| `auth_routes.py` | (none) | `/login`, `/register`, `/logout`, `/forgot`, `/reset` — web form flows |
| `mobile_auth_routes.py` | `/api/auth` | JSON login/register for React Native |
| `forget_routes.py` | `/api` | Account settings: email, username, password, deletion |
| `reset_routes.py` | `/api` | `POST /api/reset` — factory reset (requires password) |
| `chat_routes.py` | `/api` | SSE chat stream, session management |
| `admin_routes.py` | (mixed) | `GET /admin` page + `/api/admin/*` API (X-Admin-Key protected) |
| `self_core_routes.py` | `/api` | SelfCore read/update |
| `memory_routes.py` | `/api` | Memory retrieval endpoints |
| `story_routes.py` | `/api` | Story / memory narrative endpoints |
| `billing_routes.py` | `/api` | RevenueCat billing (subscriptions + credit packs) |

### Input Validation Rules

- **Username** (registration + update): `^[a-zA-Z0-9_-]+$`, 2–32 chars.
- **Password**: minimum 8 characters (enforced on web, API, and mobile endpoints).
- **Admin user IDs**: `^[a-zA-Z0-9_-]+$` validated before any DB operation.
- **Token reflection** (reset form): HTML-encoded with `_html_escape()` before insertion into HTML templates.

---

## Privacy Constraints (Non-Negotiable)

- Raw conversation logs are never stored permanently — only embeddings + emotional metadata in ChromaDB
- History imports (WhatsApp, Telegram, ChatGPT) must be processed locally; raw messages deleted immediately after embedding
- Social Mode (companion-to-companion) is always opt-in — default OFF, explicit user consent required
- No human operator can access user conversations (admin endpoints access metadata/tier, not conversation content in cleartext)
- Visual data (Ambient Vision): insights stored, never raw video/frames
