# Anjo Scaffold — AI Companion Starter

Production-ready FastAPI + LangGraph + React Native scaffold for building AI companion apps.

This scaffold gives you the full infrastructure for a persistent, emotionally-aware AI companion — auth, session management, ChromaDB memory, a LangGraph conversation graph, billing, and a React Native mobile client. You bring the personality: implement two stub functions to define who your companion is and how it grows over time.

---

## What's Included

- **FastAPI backend** — auth, rate limiting, security headers, admin panel, SSE streaming chat
- **LangGraph conversation graph** — perceive → gate → retrieve → appraise → respond pipeline
- **Three-tier memory** — ChromaDB (long-term), per-user journal (working), in-session state (ephemeral)
- **SelfCore** — Pydantic model for companion personality (OCEAN traits, PAD mood, relationship state)
- **Reflection engine stub** — post-session learning pipeline (implement your own)
- **System prompt stub** — personality injection (implement your own)
- **React Native mobile client** — Expo ~54, auth, SSE chat, story/memory views, billing
- **Billing** — FastSpring integration (subscriptions + credit packs)
- **Email** — Resend API (verification + password reset)
- **Deploy scripts** — GitHub Actions CI/CD, nginx, systemd, certbot on EC2

---

## Quick Start

**Requirements**: Python 3.11+, Node 18+ (for mobile)

```bash
# Clone and install
git clone <your-fork>
cd anjo-scaffold
pip install -e ".[test]"

# Configure environment
cp .env.example .env
# Edit .env and fill in your values

# Run locally
ANJO_ENV=dev uvicorn anjo.dashboard.app:app --reload --port 8000
```

Visit `http://localhost:8000` — you should see the landing page.

### Run tests

```bash
pytest
```

---

## Architecture Overview

```
nginx → Uvicorn
  → SecurityHeadersMiddleware
  → CORSMiddleware
  → RateLimitMiddleware (sliding window, in-memory)
  → AuthMiddleware (HMAC token verification)
  → FastAPI routing

Conversation graph (LangGraph):
  perceive → gate_node ──► [retrieve?] → appraise → respond (SSE stream)
                       └──► silent (no LLM call)
```

See `CLAUDE.md` for detailed architecture documentation and `docs/` for technical deep-dives.

---

## Customization Guide

The scaffold ships two intentional stubs that you must implement:

### 1. `anjo/core/prompt_builder.py`

```python
def build_system_prompt(core, retrieved_memories, active_emotions, ...) -> tuple[str, str]:
    """
    Define your companion's persona and inject it into every conversation turn.

    Return (static_block, dynamic_block):
      - static_block  : stable persona text (prompt-cached by Anthropic)
      - dynamic_block : per-turn state (mood, memories, session context)
    """
    raise NotImplementedError("Implement your companion's system prompt")
```

This is where your companion's voice, personality, and behavioral guidelines live.

### 2. `anjo/reflection/engine.py`

```python
def run_reflection(transcript, core, user_id, session_id, ...) -> None:
    """
    Analyze the conversation and update the companion's personality state.

    Called after each session ends. Mutates core (OCEAN traits, mood,
    relationship stage, memories) and saves to disk.
    """
    raise NotImplementedError("Implement your reflection engine")
```

This is how your companion learns and grows from interactions over time.

Both files contain detailed docstrings explaining all available parameters, helper methods on `SelfCore`, and what to write back.

### Other things to customize

- `anjo/dashboard/static/` — Replace the HTML/CSS with your own frontend
- `mobile/` — Update app name, colors, and branding in `app.json` and theme files
- `anjo/core/self_core.py` — The SelfCore schema is the data model for companion state. Extend it if you need additional personality dimensions.
- `anjo/core/emotion.py` — OCC emotion classifier. Keep as-is or replace with your own intent/emotion taxonomy.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI 0.115+ / Python 3.11+ |
| Conversation | LangGraph (StateGraph) |
| LLM | Anthropic Claude Sonnet + Haiku |
| Long-term memory | ChromaDB (local disk) |
| Short-term memory | In-process session dict |
| Personality embeddings | sentence-transformers `all-MiniLM-L6-v2` |
| Database | SQLite WAL mode |
| Mobile | React Native / Expo ~54 |
| Email | Resend API |
| Billing | FastSpring |
| Deploy | EC2 + nginx + systemd + certbot |

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your values.

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `ANJO_SECRET` | Yes | HMAC signing secret — 32+ random bytes in prod |
| `ANJO_ADMIN_SECRET` | Yes | Admin panel key — use a strong random value |
| `ANJO_BASE_URL` | Yes | e.g. `https://your-domain.com` |
| `RESEND_API_KEY` | No | Email verification/reset (skipped if absent) |
| `ANJO_ENV` | No | Set to `dev` for local development |
| `PAYMENTS_ENABLED` | No | Set to `True` to enable FastSpring billing |

---

## Mobile Client

```bash
cd mobile
npm install

# Set your backend URL
echo "EXPO_PUBLIC_API_URL=http://localhost:8000" > .env.local

npx expo start
```

The mobile app connects to the backend via `/api/auth/*` and `/api/chat/*`.

---

## Deployment

GitHub Actions workflows are included in `.github/workflows/`:

- `deploy.yml` — Push-to-deploy: rsync to EC2, inject secrets, restart systemd
- `bootstrap.yml` — One-time server setup: nginx, certbot, venv, systemd service

Required GitHub secrets: `EC2_SSH_KEY`, `EC2_HOST`, `ANTHROPIC_API_KEY`, `ANJO_ADMIN_SECRET`, `RESEND_API_KEY`.

---

## Privacy Design

- Conversation content is never stored in cleartext — only semantic and emotional embeddings in ChromaDB
- Admin endpoints expose metadata and tier info, not conversation content
- Social/multi-agent mode is always opt-in (off by default)

---

## License

MIT
