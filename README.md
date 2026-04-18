# Anjo — AI Companion

An open-source AI companion with persistent memory, personality drift, and emotional intelligence.

Anjo builds a real relationship with each user over time — remembering what matters, shifting its personality based on interactions, and reflecting after every conversation to grow. This is the full system, open-sourced.

---

## What's Inside

- **FastAPI backend** — auth, rate limiting, security headers, admin panel, SSE streaming chat
- **LangGraph conversation graph** — perceive → gate → retrieve → appraise → respond pipeline
- **Personality system** — OCEAN traits + PAD mood with per-user drift (±0.25 from a frozen baseline)
- **Three-pass reflection engine** — post-session extraction → emotional analysis → relational significance
- **Dual-embedding memory** — semantic + emotional vectors in ChromaDB, skeptical framing by confidence
- **Memory graph** — typed nodes (fact, preference, commitment, thread) with auto-supersession and contradiction detection
- **OCC emotion appraisal** — per-emotion carry and decay across turns, 9 mood-driven stances
- **SelfCore** — per-user personality state that evolves over the relationship lifecycle
- **React Native mobile client** — Expo ~54, auth, SSE chat, story/memory views, billing
- **Billing** — RevenueCat integration (subscriptions + credit packs)
- **Email** — Resend API (verification + password reset)
- **Deploy scripts** — GitHub Actions CI/CD, nginx, systemd, certbot on EC2

---

## Quick Start

**Requirements**: Python 3.11+, Node 18+ (for mobile)

```bash
git clone https://github.com/kevinconquerer/anjo-ai-companion
cd anjo-ai-companion
./setup.sh
```

`setup.sh` checks Python version, creates a virtual environment, installs dependencies, and copies `.env.example` → `.env`.

Then edit `.env` and start the server:

```bash
source .venv/bin/activate
ANJO_ENV=dev uvicorn anjo.dashboard.app:app --reload --port 8000
```

Visit `http://localhost:8000`.

### Run tests

```bash
pytest
```

---

## Architecture

```
                         ┌─────────────────────────────────┐
React Native (mobile/)   │  FastAPI backend (port 8000)    │
   ↕ /api/auth/*         │                                 │
   ↕ /api/chat/* (SSE)   │  nginx → Uvicorn                │
                         │    → SecurityHeadersMiddleware   │
Browser (static/)        │    → CORSMiddleware              │
   ↕ HTTP / SSE          │    → RateLimitMiddleware         │
                         │    → AuthMiddleware (HMAC)       │
                         │    → FastAPI routing             │
                         └────────────┬────────────────────┘
                                      │
                         ┌────────────▼────────────────────┐
                         │  LangGraph conversation graph   │
                         │                                 │
                         │  perceive → gate_node ──► retrieve → appraise → respond (SSE)
                         │                      └──► silent (no LLM call)             │
                         └────────────┬───────────────────────────────────────────────┘
                                      │
               ┌──────────────────────┼──────────────────────┐
               ▼                      ▼                       ▼
        SQLite (WAL)           ChromaDB (disk)         JSON files
        users, credits         semantic + emotion       self_core/
        subscriptions          memory embeddings        current.json
```

See `CLAUDE.md` for detailed architecture documentation and `docs/` for technical deep-dives.

---

## How the Personality System Works

Anjo's personality is a two-layer system:

- **Baseline** — frozen Big Five (OCEAN) traits that define who Anjo fundamentally is
- **Overlay** — per-user drift that shifts ±0.25 from the baseline based on interaction history

Each conversation session runs a three-pass reflection after it ends:
1. **Extraction pass** — facts, preferences, commitments the user mentioned
2. **Emotional pass** — how the conversation felt, emotional significance
3. **Relational pass** — what this means for the relationship arc, whether to advance the relationship stage

Memory is stored as dual embeddings (semantic + emotional) with confidence-based framing — high-confidence memories surface as "I remember", mid-confidence as "I have a sense", low-confidence are omitted rather than hallucinated.

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
| Billing | RevenueCat |
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
| `PAYMENTS_ENABLED` | No | Set to `True` to enable RevenueCat billing |
| `REVENUECAT_WEBHOOK_SECRET` | No | Required if `PAYMENTS_ENABLED=True` |

---

## Mobile Client

`setup.sh` creates `mobile/.env.local` automatically. To start the mobile client:

```bash
cd mobile
npm install
npx expo start
```

Update `EXPO_PUBLIC_API_URL` in `mobile/.env.local` if your backend runs on a different address.

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

## Using with Claude Code

This repo includes `CLAUDE.md` which gives Claude Code full context on the architecture, auth model, conversation graph, and design decisions.

```bash
claude
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT
