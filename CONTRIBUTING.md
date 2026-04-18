# Contributing to Anjo

Thanks for your interest in contributing. Anjo is an open-source AI companion — contributions that improve the memory system, conversation graph, personality model, infrastructure, or developer experience are welcome.

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

## Branch Naming

```
feat/short-description       # new feature
fix/short-description        # bug fix
docs/short-description       # documentation only
refactor/short-description   # refactoring, no behaviour change
test/short-description       # adding or fixing tests
```

All branches cut from `main`. One logical change per branch.

## Submitting Pull Requests

1. Fork the repo and cut a branch using the naming convention above
2. Make your changes
3. Run the test suite: `pytest`
4. Run linting: `ruff check .`
5. Open a PR — use the PR template, it's short

PRs should be focused. One logical change per PR.

### What makes a good PR

- Fixes a real bug or improves the system
- Includes or updates tests where appropriate
- Passes CI (tests + lint run automatically on every PR)
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
