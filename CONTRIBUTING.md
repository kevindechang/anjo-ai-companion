# Contributing to Anjo Scaffold

Thanks for your interest in contributing. This is a scaffold — its job is to be a clean, well-documented starting point for AI companion apps. Contributions that improve that goal are welcome.

---

## What This Repo Is (and Isn't)

The scaffold provides infrastructure: auth, session management, ChromaDB memory, a LangGraph conversation graph, billing hooks, and a React Native mobile client.

Two files are **intentional stubs** — they are NOT implemented here by design:

- `anjo/core/prompt_builder.py` — define your companion's voice and persona
- `anjo/reflection/engine.py` — define how your companion learns from conversations

Contributions to the infrastructure are welcome. Contributions that fill in these stubs with a specific personality are out of scope — that's your app, not the scaffold.

---

## Running Locally

```bash
# 1. Clone and install
git clone https://github.com/your-org/anjo-scaffold.git
cd anjo-scaffold
./setup.sh

# 2. Edit .env with your values (at minimum: ANTHROPIC_API_KEY, ANJO_SECRET, ANJO_ADMIN_SECRET)

# 3. Start the backend
ANJO_ENV=dev uvicorn anjo.dashboard.app:app --reload --port 8000

# 4. Run tests
pytest
```

For the mobile client:

```bash
cd mobile
npm install
npx expo start
```

The mobile app requires the backend to be running and `EXPO_PUBLIC_API_URL` set in `mobile/.env.local`.

---

## Submitting Issues

Before opening an issue:

- Check existing issues to avoid duplicates
- Make sure you can reproduce the problem on a clean clone

Use the issue templates — they prompt for the information that makes issues actionable quickly.

---

## Submitting Pull Requests

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Run the test suite: `pytest`
4. Open a PR with a clear description of what and why

PRs should be focused. One logical change per PR.

### What makes a good PR

- Fixes a real bug or improves developer experience
- Keeps the scaffold generic — no app-specific logic
- Includes or updates tests where appropriate
- Doesn't break existing tests

---

## Code Style

**Python (backend)**

- Follow the patterns already in the codebase
- Type annotations on all function signatures
- Docstrings on public functions
- Keep functions short and focused (under 50 lines where possible)
- No hardcoded secrets — use environment variables

Run the existing tests to verify nothing is broken:

```bash
pytest
```

**TypeScript / React Native (mobile)**

- Follow the patterns in `mobile/lib/` and `mobile/app/`
- Prefer functional components and hooks
- Keep components small — split if a file grows past ~200 lines
- No `any` types without a comment explaining why

---

## Using Claude Code

This repo includes `CLAUDE.md` which gives Claude Code full context on the codebase. If you use Claude Code for your contributions, it will have a complete picture of the architecture.

```bash
claude    # Start Claude Code — reads CLAUDE.md automatically
```

---

## Questions

Open a discussion or an issue. This is a small project — there's no separate forum.
