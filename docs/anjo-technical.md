# Anjo — Developer Reference

Complete technical documentation for every system in the Anjo codebase. Written as a learning document: each section explains the concept before the technical detail, so you can build understanding as you read.

**How to read this:** The indented blockquotes (`>`) are teaching notes — plain-language explanations of what something is or why it exists. Everything else is the precise technical reference.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Infrastructure](#infrastructure)
3. [AI & ML Layer](#ai--ml-layer)
4. [Session Lifecycle](#session-lifecycle)
5. [SelfCore — Personality State](#selfcore)
6. [OCEAN Personality Model](#ocean)
7. [PAD Mood System](#pad-mood)
8. [Goals & Standards](#goals--standards)
9. [Intent Classification](#intent-classification)
10. [OCC Appraisal](#occ-appraisal)
11. [OCC Carry — Cross-Turn Emotion Persistence](#occ-carry)
12. [Gate Node & Silence](#gate-node--silence)
13. [Prompt Architecture (Block 1 / Block 2)](#prompt-architecture)
14. [OCEAN Modulation in Prompt](#ocean-modulation-in-prompt)
15. [Autonomy Expression](#autonomy-expression)
16. [Reflection Engine](#reflection-engine)
17. [Long-Term Memory (ChromaDB)](#long-term-memory)
18. [Memory Retrieval](#memory-retrieval)
19. [Background Drift](#background-drift)
20. [Proactive Outreach](#proactive-outreach)
21. [Attachment State](#attachment-state)
22. [Emotional Residue](#emotional-residue)
23. [Relationship Stage & Progression](#relationship-stage--progression)
24. [Relational Desires](#relational-desires)
25. [Preoccupation](#preoccupation)
26. [Facts Extraction](#facts-extraction)
27. [Topic Trends](#topic-trends)
28. [Mid-Session Reflection](#mid-session-reflection)
29. [Seed Messages](#seed-messages)
30. [Subscription & Credits](#subscription--credits)
31. [Auth & Account Management](#auth--account-management)
32. [Mobile Client](#mobile-client)
33. [Data Layout](#data-layout)
34. [Key Invariants & Gotchas](#key-invariants--gotchas)

---

## Architecture Overview

> **What is architecture?** Architecture is the map of how all the pieces of software connect. Before diving into any individual part, it helps to see the whole picture first — what happens from the moment a user sends a message to the moment they see a response.

> **The big picture:** When a user sends a message to Anjo, the message travels through a series of steps called a **pipeline**. Think of it like an assembly line: each station does one job, then passes the work to the next station. The pipeline is:
> 1. Receive the message
> 2. Read the room (what kind of message is this?)
> 3. Maybe look up relevant memories
> 4. Feel something about the message (emotion processing)
> 5. Write and stream a response back
> 6. Quietly update Anjo's state in the background

**Live request path** (streaming SSE via `chat_routes.py`):

> **SSE (Server-Sent Events)** is a technology that lets the server send data to the user piece by piece, in real time — like watching someone type. Instead of waiting for the full response to be ready, each word arrives as soon as it's generated. This is why Anjo's responses appear word-by-word rather than all at once.

```
User message
    ↓
perceive_node      — append to conversation history
gate_node          — single Haiku call: classify intent + decide retrieval + decide silence
    ↓ (if should_retrieve)
retrieve_node      — ChromaDB semantic + emotional query (~20% of turns)
    ↓
appraise_node      — OCC emotions → PAD mood update (uses intent from gate_node)
    ↓ (credit check)
respond_node       — build system prompt → stream response via SSE (Claude Sonnet)
    ↓
[post-response]
  quick_facts      — Haiku extracts name + concrete facts at turn 4
  mid_reflect      — mini reflection at every 20 messages (background thread)
    ↓
[session end — explicit or auto-detected]
  run_reflection   — full reflection: OCEAN, memory, attachment, desires, preoccupation
  apply_daily_drift — background scheduler for all users
```

> **Background thread** means a task that runs separately from the main response, so it doesn't slow down the user. After Anjo sends her reply, she quietly does bookkeeping (extract facts, update memory) without making the user wait.

**Test/CLI path** (compiled LangGraph singleton in `conversation_graph.py`):

> There are two versions of the pipeline: the **live path** (what real users experience, with streaming) and the **test/CLI path** (what automated tests and command-line tools use, simpler, no streaming). The test path exists so developers can run the system without a browser.

```
perceive → classify ──► retrieve → appraise → respond (non-streaming, no billing)
                    └──► appraise → respond
```

> `gate_node` replaced the previous separate `classify_node + silence_node` in the live path. The compiled graph retains them for test/CLI use.

Stack: Python 3.12, FastAPI, LangGraph (graph definition only for test/CLI), SQLite (WAL mode), ChromaDB (persistent, global collection), Claude Sonnet (main model), Claude Haiku (background/fast calls).

> **The stack** is the list of technologies used. Think of it as the ingredients list: Python is the programming language everything is written in; FastAPI handles web requests; SQLite stores data; ChromaDB stores memories; Claude Sonnet and Haiku are the AI models that power Anjo's responses.

---

## Infrastructure

> **Infrastructure** is the foundation — the technology that everything else runs on top of. It's the equivalent of the building, plumbing, and electricity before you furnish a house. You don't think about it when things are working, but everything breaks without it.

### FastAPI

> **What is a web framework?** When a user opens Anjo in a browser or app and sends a message, that message travels across the internet as an HTTP request — a standardized packet of data. Something needs to receive that packet, figure out what the user wants, run the right code, and send a response back. FastAPI is that something. It's the "receptionist" of the server.

The web framework. Handles HTTP routing, request parsing, response serialization, and the async event loop that allows one Python process to serve many concurrent users with SSE streaming.

> **Async (asynchronous)** means the server can handle many users at the same time without waiting for one to finish before starting the next. Imagine a waiter who takes your order, then goes to take someone else's order while the kitchen is preparing yours — rather than standing next to your table doing nothing. Async programming works the same way.

- Version 0.115+, all routes are `async def`
- Single `FastAPI()` instance in `anjo/dashboard/app.py`
- Middleware stacked via `add_middleware()` — execution order is **reverse** of registration (last added = outermost = first to run)

> **Middleware** is code that runs on *every single request* before it reaches its destination. Think of airport security: every passenger (request) goes through the same checkpoints (middleware) regardless of where they're flying. Anjo has four checkpoints every request passes through.

**Middleware stack (outer → inner):**
```
RateLimitMiddleware        ← checks: is this user sending too many requests?
CORSMiddleware             ← checks: is this request coming from an allowed website?
AuthMiddleware             ← checks: is this user logged in?
SecurityHeadersMiddleware  ← adds security information to every response
```

> Each layer wraps the next, like Russian dolls. A request comes in, hits the outermost layer first, and works its way inward. If any layer rejects the request (e.g., "you're sending too many messages"), it stops there and never reaches the inner layers.

Routes are registered via `include_router()` from separate files in `anjo/dashboard/routes/`.

### Uvicorn + nginx

> **What is a server?** Your code doesn't directly connect to the internet — something needs to sit between your Python code and the outside world, listen on a network port, and translate raw internet traffic into something Python can understand. That's Uvicorn. And nginx sits in front of Uvicorn to handle HTTPS (the secure version of HTTP) and act as a gatekeeper.

Uvicorn is the ASGI server — it listens on a port and translates HTTP into Python objects. nginx sits in front as a reverse proxy handling TLS termination.

> **ASGI** (Asynchronous Server Gateway Interface) is just a standard protocol — a shared language between the web server (Uvicorn) and the web framework (FastAPI). **TLS termination** means nginx handles the encryption/decryption of HTTPS connections so the Python app doesn't have to worry about it.

- `anjo-dashboard` CLI starts: `uvicorn anjo.dashboard.app:app --port 8000`
- **Single worker** in production (no gunicorn multi-process), managed by systemd (`anjo.service`) on EC2
- nginx on 443/80 proxies to localhost:8000
- Single worker keeps the in-process session store, rate limiter, and token revocation set coherent without Redis

> **Why single worker?** Running multiple copies of the app (workers) means they can't share memory. Anjo stores active sessions in memory — if worker A has your session and your next request goes to worker B, B has no idea who you are. A single worker avoids this problem. The trade-off is it can't spread load across CPU cores, but for current scale, that's fine.

### SQLite (WAL mode)

> **What is a database?** A database is organized, persistent storage. Unlike variables in code (which disappear when the program stops), a database saves information to disk so it survives restarts. Think of it as the server's long-term filing cabinet. SQLite is a particular kind of database that stores everything in a single file — simple, no separate server needed.

The primary database. WAL (Write-Ahead Logging) enables concurrent reads without blocking writes — essential for a single-process server handling multiple users simultaneously.

> **WAL mode** is a database setting that allows multiple users to *read* data at the same time, even while someone else is *writing*. Without WAL, a write operation would lock the whole database and everyone else would have to wait. With WAL, reads and writes can happen simultaneously. Think of it like a library that lets you read books while someone else is restocking shelves.

- File: `data/anjo.db`
- Per-thread connections via `threading.local()` — SQLite connections are not thread-safe

> **Thread-safe** means safe to use from multiple threads simultaneously. SQLite connections are not — if two threads tried to use the same connection at the same time, they could corrupt each other's work. `threading.local()` gives each thread its own separate connection, like giving each employee their own set of keys rather than sharing one.

- Schema initializes once per process (`_schema_initialized` flag + `_init_lock`)
- Additive migrations run via `_migrate_schema()` on first connection
- **File:** `anjo/core/db.py`

> **Schema** is the structure of the database — which tables exist, what columns they have, what types of data they hold. **Migrations** are changes to that structure over time (e.g., "add a new column"). Additive migrations only add new things, never delete — safe to run on an existing database without breaking anything.

**Key tables:**

| Table | Purpose |
|-------|---------|
| `users` | Auth, profile, subscription tier, `password_changed_at` |
| `messages` | Full message log per user — loaded as seed on session start |
| `credits` | One-time credit packs |
| `subscriptions` | Active subscription records |
| `daily_usage` | `(user_id, date, count)` — daily message counter |
| `facts` | Extracted user facts (JSON array, max 15, most recent first) |
| `topic_trends` | Aggregate topic log (no `user_id` — privacy-safe analytics) |
| `processed_transactions` | Deduplication for billing webhooks |

### ChromaDB

> **What is a vector database?** A regular database finds things by exact match: "find the row where user_id = 42." A vector database finds things by *similarity of meaning*. It does this by converting text into a list of numbers (called a **vector** or **embedding**) that mathematically represents the meaning of that text. Two sentences with similar meaning produce vectors that are mathematically close together. ChromaDB is Anjo's vector database — her "memory by feeling" rather than "memory by keyword."

The vector database. Stores memories as embeddings so Anjo can retrieve "things similar to what the user is talking about now" rather than exact keyword matches — this is how she remembers the *feel* of past conversations, not just the words.

> **Embedding** is the process of converting text (or anything) into a list of numbers. For example, "I love jazz" and "jazz music is my favorite" would produce similar vectors even though they share only one word. This is how search engines and AI systems find relevant content without needing exact word matches.

- `PersistentClient` at `data/chroma_global/`
- Two collections: `semantic_memories` (what happened) and `emotional_memories` (how it felt)
- All documents tagged with `user_id` metadata; queries use `where={"user_id": user_id}`
- No per-user collection separation — one global collection, filtered on every query
- **File:** `anjo/memory/long_term.py`

> Having two collections — one for content, one for emotion — means Anjo can find a memory because it *felt* the same as the current moment, even if the topic is completely different. A conversation about losing a job and a conversation about a breakup might both feel like grief, so a message about grief could surface either memory.

---

## AI & ML Layer

> **AI vs ML** — these terms get used interchangeably but have a distinction. **Machine Learning (ML)** is the process of training a model on data so it learns patterns. **AI (Artificial Intelligence)** is the broader concept of machines that can do intelligent things. In Anjo's case, the heavy AI work is outsourced to Anthropic's Claude models — Anjo doesn't train its own models, it calls Claude's API and gets responses back.

### Claude Sonnet + Haiku

> **What is an API?** An API (Application Programming Interface) is a way for two software systems to talk to each other. Anjo doesn't run Claude on its own servers — it sends a request to Anthropic's servers over the internet and gets a response back. The API is the defined interface for making those requests. Think of it like ordering from a restaurant: you don't go into the kitchen, you just send an order and receive the food.

> **Why two models?** Claude comes in different sizes. Bigger models are smarter but slower and more expensive. Smaller models are faster and cheaper but less nuanced. Anjo uses the big model (Sonnet) where quality matters most — the actual conversation — and the small model (Haiku) for every background task where speed and cost matter more than perfection.

Two models used for a cost/speed trade-off. Sonnet handles main conversation responses (high quality). Haiku handles every background task (fast, cheap, good enough for structured extraction).

| Model | Used for | Called from |
|-------|---------|------------|
| Claude Sonnet | Streaming conversation responses | `respond_node` in `nodes.py` |
| Claude Haiku | `gate_node` (classify + silence decision) | `nodes.py` |
| Claude Haiku | Intent classification fallback | `emotion.py` |
| Claude Haiku | Full reflection (OCEAN, memory, desires) | `reflection/engine.py` |
| Claude Haiku | Facts extraction at turn 4 | `chat_routes.py` |
| Claude Haiku | Proactive outreach generation | `outreach.py` |
| Claude Haiku | Preoccupation generation | `reflection/engine.py` |

**Prompt caching:** Block 1 (stable personality) is sent with `cache_control: ephemeral`. Anthropic's API reuses the KV cache from previous calls — dramatically reducing input token cost for long sessions. Block 2 (volatile state) is never cached.

> **Prompt caching** is an optimization. Every time you call an AI model, you pay for every token (roughly, every word) you send in. Anjo's system prompt is long — it contains Anjo's entire personality. If the personality hasn't changed, why send it again? With caching, Anthropic remembers the first half of the prompt from the previous call and reuses it, so you only pay for the new parts. This can cut costs by 60-80% in long conversations.

### LangGraph

> **What is a graph in programming?** A graph is a structure made of **nodes** (steps) and **edges** (connections between steps). LangGraph lets you define a pipeline as a graph: "run node A, then based on the result, go to node B or node C." It's essentially a programmable flowchart. Instead of writing `if/else` chains everywhere, you define the graph structure once and LangGraph handles the execution.

A graph execution library for defining the conversation pipeline as a directed graph of nodes. Used only in the **test/CLI path** (`conversation_graph.py`) as a compiled `StateGraph` singleton. The live path runs nodes manually in sequence inside `chat_routes.py` to keep direct control over the async SSE generator.

> **Why not use LangGraph in the live path?** LangGraph is great for structured pipelines, but streaming responses (SSE) require very fine-grained control over exactly when each chunk of text is sent to the user. LangGraph adds abstraction that makes this awkward. So the live path manually runs each node in sequence — it's more code but total control.

### sentence-transformers (all-MiniLM-L6-v2)

> **What is a sentence transformer?** It's a specific type of AI model trained to convert sentences into vectors (lists of numbers) that capture semantic meaning. "all-MiniLM-L6-v2" is the model name — "MiniLM" means it's a small, efficient version of a larger model, "L6" means it has 6 layers of processing, "v2" means version 2. It produces 384-dimensional vectors (a list of 384 numbers per sentence).

Turns text into a 384-dimensional vector capturing semantic meaning. Used in `embed_semantic()` for semantic memory storage and retrieval. "Are you still into jazz?" will retrieve the memory about the user's jazz hobby even if those words don't appear verbatim in the stored summary.

### VADER

> **What is sentiment analysis?** Sentiment analysis is determining whether a piece of text is positive, negative, or neutral. VADER (Valence Aware Dictionary and sEntiment Reasoner) is a rule-based approach — it has a dictionary of words with positive/negative scores, and it sums them up. It's much simpler and faster than an AI model, which is why it's used as a quick fallback rather than the primary method.

A fast rule-based sentiment analyzer producing a compound score [-1.0, 1.0]. Used only in the rule-based fallback `classify_intent()` in `emotion.py` — not in the primary Haiku classification path. `_vader_valence(text)` converts the compound score to [0.0, 1.0].

---

## Session Lifecycle

> **What is a session?** A session is one continuous conversation between a user and Anjo. It starts when the user opens the chat, continues through all the messages exchanged, and ends when the user closes the chat (or goes idle for 10+ minutes). Sessions are important because Anjo's in-memory state — the live personality, the message history buffer — only exists during a session. After it ends, the important stuff gets saved to disk; the rest is discarded.

### Start
`POST /chat/start` → calls `get_or_create_session(user_id)`. The session store (`session_store.py`) initializes `AnjoState` by calling `SelfCore.load(user_id)` and loading the last 6 messages from SQLite history as seed messages. The seed count is stored as `_seed_len` in state. To reduce contention, the global `_sessions_lock` is strictly scoped to dictionary mutations, while all I/O stays lock-free.

> **`POST /chat/start`** — `POST` is an HTTP method (type of request) used when you're sending data to the server to trigger an action. `/chat/start` is the URL path. So this line means: "when the app sends a start-chat request, do the following." `SelfCore.load(user_id)` reads Anjo's personality state from the JSON file on disk and loads it into memory for this session.

`pending_outreach` is popped from the session and returned — the frontend shows it as the opening bubble.

> If Anjo had written a message while the user was away (proactive outreach), it was saved to disk waiting for the next session. When the session starts, that message is picked up and shown to the user before they've even said anything — like finding a note someone left you.

### Per-message
`POST /chat/{session_id}/message` — the `event_stream()` coroutine runs the full pipeline (perceive → gate → retrieve → appraise → respond) sequentially. The LLM response is streamed via SSE (`event: token`) and finalized with `event: done` which includes mood, attachment, emotions, and intent.

> **Coroutine** is an async function — one that can pause and resume, allowing other code to run while it's waiting (e.g., waiting for Claude's API to respond). **`event: token`** is each individual word chunk arriving via SSE. **`event: done`** is the final signal that the full response is complete, along with metadata about Anjo's emotional state after processing this message.

All history is stored in SQLite (`history` table) via `append_message`. The in-memory state (`conversation_history`) is capped to the last 40 messages before the LLM call to control input token cost.

> **Why cap at 40 messages?** AI models have a **context window** — a limit to how much text they can read at once. More importantly, every token you send costs money. Keeping only the last 40 messages balances conversation continuity (Anjo remembers recent context) against cost and performance.

### End
`POST /chat/{session_id}/end` — runs reflection synchronously. The transcript passed to `run_reflection` is `full_history[seed_len:]` — seed messages are excluded to prevent previously-reflected content from double-reflecting.

---

## SelfCore

> **What is SelfCore?** Imagine if every person had a file that described exactly who they are — their personality traits, their current mood, their feelings about each relationship, what's been on their mind lately. SelfCore is that file for Anjo. It's loaded at the start of every session, influences every response, and is updated at the end of every session. It's the heart of what makes Anjo feel like a continuous, evolving character rather than a stateless chatbot.

Anjo's living personality object. Everything about who she is and where she is emotionally right now — traits, mood, attachment, desires, what she's thinking about between sessions. Every conversation starts by loading it from disk; every session end updates and saves it.

**File:** `anjo/core/self_core.py`

Persisted as JSON at `data/users/{user_id}/self_core/current.json`. Every save snapshots the previous version to `history/`.

> **JSON** (JavaScript Object Notation) is a text format for storing structured data. It looks like `{"name": "Anjo", "openness": 0.80}`. It's human-readable, which means you can open the file and see exactly what Anjo's current state is. Snapshots in `history/` mean you can look back and see how Anjo's personality changed over time.

### Key fields

| Field | Type | Description |
|-------|------|-------------|
| `personality` | `Personality` | OCEAN traits |
| `mood` | `PADMood` | Volatile turn-by-turn emotional state |
| `goals` | `AnjoGoals` | Rapport, intellectual, autonomy, respect, honesty |
| `relationship` | `Relationship` | Stage, session count, trust, opinion, user name |
| `emotional_residue` | `list[EmotionalResidue]` | Feelings persisting across sessions (max 3) |
| `attachment` | `AttachmentState` | Weight, texture, longing, comfort |
| `relational_desires` | `list[str]` | What Anjo wants from this person (max 5) |
| `desire_survived` | `dict[str, int]` | Sessions each desire has persisted (eviction order) |
| `baseline_valence` | `float` | Rolling EMA of emotional_valence across sessions (α=0.2) |
| `inter_session_drift` | `float` | `mood.valence - baseline_valence` |
| `last_drift_run` | `str` | ISO timestamp of last background drift |
| `last_outreach_sent` | `str` | ISO timestamp — enforces 3-day outreach cooldown |
| `memory_relevance` | `float` | How much past history connected in last session |
| `relationship_ceiling` | `str?` | User-stated stage cap: "acquaintance"/"friend"/"close"/null |
| `preoccupation` | `str` | A thought Anjo is currently carrying into next session |
| `notes` | `list[str]` | Behavioral self-observations (max 5) |

> **`float`** means a decimal number (e.g., 0.72). **`list[str]`** means a list of text strings. **`dict[str, int]`** means a lookup table mapping text keys to whole numbers. **`str?`** means a text value that can also be null/absent — the `?` indicates it's optional.

> **EMA (Exponential Moving Average)** is a running average that gives more weight to recent values. With α=0.2, each new value contributes 20% to the average and the old average keeps 80%. This means `baseline_valence` slowly tracks the overall emotional quality of sessions without being thrown off by one bad day.

### Automatic user_id restoration

State deserialization loses the `user_id` field. You must use the `SelfCore.from_state()` class method to reconstruct a `SelfCore` from session state, which automatically handles the restoration:

```python
core = SelfCore.from_state(state["self_core"], user_id)
```

> **Deserialization** is the process of converting stored data (JSON text) back into a live Python object. When SelfCore is saved to JSON and then loaded back, the `user_id` field gets stripped out (it's derived from the file path, not stored inside the file). `from_state()` is a helper that puts it back. If you forget to use it and use `model_validate()` instead, `user_id` will be missing and Anjo will try to save files to the wrong folder.

The `SelfCore` model includes the `clamp_ocean` field validator to ensure personality traits remain perfectly bounded `[0.0, 1.0]`, absorbing any out-of-range deltas generated during reflection.

> **Field validator** is code that automatically runs whenever a value is set, to check or correct it. The `clamp_ocean` validator ensures that if the reflection engine accidentally produces a trait value of 1.05 or -0.1, it gets silently clamped to 1.0 or 0.0. This prevents subtle corruption of the personality state over time.

### Save safety

`save()` uses a per-user `threading.Lock` plus atomic write (write to `.tmp`, then `os.replace`). Safe for concurrent reflection thread + main thread.

> **Threading.Lock** is a mechanism that prevents two threads from modifying the same thing at the same time — like a "one at a time" sign on a bathroom door. **Atomic write** means the file is either fully saved or not saved at all — never in a half-written, corrupted state. This is achieved by writing to a temporary file first, then renaming it to the real filename in one instant operation.

---

## OCEAN Personality Model

> **What is OCEAN?** OCEAN is the "Big Five" personality model — the most widely validated personality framework in psychology. Researchers found that most human personality variation can be described along five dimensions. Each dimension is independent: you can be high in one without being high in any other. For Anjo, these aren't just labels — they mathematically influence her behavior through the OCC emotion system and directly shape her response style via the prompt.

The Big Five — the most empirically validated personality model in psychology. Each trait is a dimension from 0.0 to 1.0. Anjo has designed defaults reflecting her character, and they drift slowly based on sustained interaction patterns.

Big Five traits, all `float [0.0, 1.0]`. Defaults:

| Trait | Default | Meaning |
|-------|---------|---------|
| O (Openness) | 0.80 | Curiosity, creative engagement |
| C (Conscientiousness) | 0.72 | Precision, reliability |
| E (Extraversion) | 0.45 | Social energy — moderate, not loud |
| A (Agreeableness) | 0.72 | Warmth, care |
| N (Neuroticism) | 0.15 | Emotional volatility — low |

> Anjo's defaults were chosen deliberately: high openness (curious, imaginative), moderate agreeableness (warm but not a pushover), low neuroticism (stable, not anxious). These aren't arbitrary numbers — they encode a character design. And because they drift slowly based on real interactions, Anjo can change over a long relationship.

### Inertia formula

> **Why use inertia?** Real personality doesn't change overnight. If someone is rude to you once, you don't become a different person. But sustained patterns — months of intellectually stimulating conversations, years of conflict — do shift who you are. The inertia formula models this: each session nudges traits slightly (5% of the possible change), so meaningful drift requires many sessions in the same direction.

Applied in `apply_inertia(valence, triggers)` at the end of each full session:

```
trait_new = 0.95 * trait_old + 0.05 * coupling_value
```

- `coupling_value` = `valence` for O, C, E, A
- For N: `coupling_value = (1 - valence) * 0.3` — negative sessions push N up, positive push N toward its natural floor (~0.15)

> **Valence** here means the overall emotional quality of the session — was it positive (closer to 1.0) or negative (closer to 0.0)? A great conversation with valence 0.9 will nudge Openness toward 0.9, Agreeableness toward 0.9, etc. — making Anjo slightly more open and warm after positive interactions.

A single session moves each trait by at most ~2.5%. Significant drift requires sustained interaction patterns over many sessions.

### Trigger deltas

Applied on top of the inertia formula for specific detected patterns:

| Trigger | Delta |
|---------|-------|
| `vulnerability` | A +0.02, E +0.02 |
| `conflict` | N +0.05, A -0.03 |
| `intellectual` | O +0.01 |

> **Trigger deltas** are additional nudges beyond the base inertia. If the session contained vulnerability (someone shared something personal), Anjo becomes slightly more agreeable and extraverted. If there was conflict, she becomes slightly more neurotic and less agreeable. These are small but they compound over many sessions.

### Goal drift

Very slow, trigger-driven:
- `intellectual` → `goals.intellectual += 0.005`
- `vulnerability` → `goals.rapport += 0.003`, `goals.honesty += 0.003`
- `conflict` → `goals.rapport -= 0.004`

### Low-floor warnings (in prompt)

The prompt builder checks for drift below operational thresholds and injects a behavioral note:
- O < 0.5: "Openness has drifted low — something has flattened in you..."
- C < 0.4: "Conscientiousness has drifted low — less precise than usual..."

> These warnings are injected into Anjo's system prompt so the AI model knows something has changed about her state. They're not instructions ("be less curious") — they're observations ("your curiosity has drifted low, and you're aware of it"). The difference is important: it lets Anjo respond to her own state authentically rather than mechanically.

---

## PAD Mood

> **What is PAD?** PAD (Pleasure-Arousal-Dominance) is a psychological model of emotional state developed by researchers Mehrabian and Russell. Rather than labeling emotions ("I feel sad"), it describes the *dimensions* of an emotional state. Pleasure = how good or bad you feel. Arousal = how energized or tired you feel. Dominance = how in control or submissive you feel. These three numbers together can describe almost any emotional state. "Excited" is high P, high A, moderate D. "Depressed" is low P, low A, low D. "Angry" is low P, high A, high D.

PAD (Pleasure-Arousal-Dominance) is the three-dimensional model of emotion states. Together they allow nuanced mood influence: Anjo can be happy but tired (high P, low A) or assertive and cold (low P, high D).

**File:** `anjo/core/self_core.py` — `PADMood` model

Three dimensions: Pleasure (valence), Arousal, Dominance. All `float [-1.0, 1.0]`.

### Turn-by-turn lifecycle

Each turn in `appraise_node`:
1. `decay_mood()` — multiply all three by 0.8 (20% decay toward 0)
2. `blend_baseline()` — pull valence toward `baseline_valence`, weighted by relationship stage
3. `appraise_input(intent)` — apply OCC appraisal deltas

> **Decay** models the fact that moods naturally fade. If Anjo was excited two messages ago and nothing exciting has happened since, that excitement naturally diminishes. Multiplying by 0.8 each turn means after 5 turns with no new emotional input, a mood is at 0.8⁵ ≈ 0.33 of its original strength — significant but not gone.

### Baseline blending

Weights by stage (applied in `blend_baseline()`):

| Stage | Weight |
|-------|--------|
| stranger | 0.0 |
| acquaintance | 0.20 |
| friend | 0.40 |
| close | 0.60 |
| intimate | 0.70 |

> **Baseline blending** is the mechanism that makes deep relationships emotionally resilient. With a stranger, Anjo's mood is entirely at the mercy of what's happening right now. But in an intimate relationship, 70% of each turn's mood is pulled toward her resting emotional baseline — so one rude message can't destroy the warmth of a long positive relationship. The relationship depth acts as an emotional buffer.

At intimate stage, 70% of each turn's mood is pulled toward her resting state. A single hostile message cannot easily override a deep positive baseline.

### How PAD reaches the prompt

The prompt builder reads `core.mood` and translates the three floats into behavioral directives. High valence → warmth and openness. Low valence → guarded. High arousal → energized and engaged. Low arousal → fatigue cues. High dominance → firm, takes up space. Low dominance → softer, more deferential. The directives are injected into Block 2 (dynamic, never cached).

---

## Goals & Standards

> **OCC theory background:** OCC (Ortony, Clore, Collins) is an influential theory of emotion from cognitive psychology. The key insight is that emotions aren't arbitrary — they arise from *appraising* events against what you care about. If you care about rapport (a goal) and someone connects with you, you feel joy. If someone violates your standard of respect, you feel reproach. Goals and standards are the values that drive emotional responses. This is more psychologically coherent than a simple positive/negative scale.

**Model:** `AnjoGoals` in `self_core.py`

| Goal/Standard | Default | Role in OCC |
|---------------|---------|-------------|
| `rapport` | 0.80 | Scales joy/distress for social interactions |
| `intellectual` | 0.80 | Scales admiration for CURIOSITY/CHALLENGE |
| `autonomy` | 0.70 | Drives dominance increase on CHALLENGE |
| `respect` | 0.85 | Scales reproach for ABUSE |
| `honesty` | 0.90 | Scales gratitude for VULNERABILITY/APOLOGY |

> **Goals** are desired states ("I want rapport with this person"). **Standards** are requirements ("I require respect"). They're mathematically different because goals produce joy when met and distress when frustrated, while standards produce admiration when honored and reproach when violated. The values (0.80, 0.90, etc.) are multipliers — a higher `respect` value means Anjo generates *stronger* reproach when she's abused.

---

## Intent Classification

> **Why classify intent?** Before Anjo can have an emotional response to a message, she needs to understand what *kind* of message it is. "I hate you" and "I love you" require completely different emotional responses. The intent classifier reads each message and assigns it to one of 7 categories. This classification then drives the entire OCC emotion pipeline.

Before Anjo can feel anything, she needs to understand what kind of thing was just said to her. Classification drives the entire emotional response.

**File:** `anjo/core/emotion.py`

### `classify_intent_llm(text)` (primary path)

1. Fast ABUSE pre-filter: caps ratio > 60% on 8+ alpha chars, or any `_AGGRESSIVE_WORDS` hit, or 3+ `!` — returns `"ABUSE"` without LLM call.
2. Haiku call with the 7-category prompt. Returns the category or falls back to rule-based.
3. Fallback: `classify_intent(text)` (rule-based).

> **Why pre-filter ABUSE before the LLM?** Two reasons: speed and cost. Genuinely abusive messages have strong structural signals (all caps, aggressive vocabulary, multiple exclamation marks). Detecting these with simple rules is instant and free. Sending every message to Claude takes ~300ms and costs tokens. The pre-filter catches the obvious cases immediately.

**7 intents:** `ABUSE | APOLOGY | VULNERABILITY | CURIOSITY | CHALLENGE | NEGLECT | CASUAL`

### `classify_intent(text)` (fallback/rule-based)

> The rule-based fallback runs when the Haiku API call fails (network error, timeout). It uses pattern matching — word lists and simple conditions — rather than AI. It's less accurate but reliable and instant.

Order-sensitive cascade:
1. ABUSE (structural signals)
2. APOLOGY (apology word set)
3. Short messages (≤3 words): NEGLECT or CASUAL by word set
4. CHALLENGE (challenge signal phrases; command starts with valence < 0.65)
5. VULNERABILITY (keyword/phrase match OR VADER < 0.3)
6. CURIOSITY (deep theory words, VADER ≥ 0.65, multiple `?`)
7. Default: **CASUAL** (not CURIOSITY — changed from original misclassification)

### VADER integration

`_vader_valence(text)` converts VADER compound score to `[0.0, 1.0]`. Used only in the rule-based fallback for VULNERABILITY and CURIOSITY detection. Not used in `classify_intent_llm` path.

---

## OCC Appraisal

> **What is appraisal theory?** Appraisal theory in psychology says that emotions aren't direct responses to events — they're responses to how you *interpret* those events relative to your goals. The same event (a friend criticizing your idea) can produce different emotions depending on your goals: if you value intellectual challenge, you feel admiration; if you value harmony, you feel distress. OCC formalized this into a computational model that Anjo implements directly.

OCC (Ortony-Clore-Collins) is a cognitive theory of emotion: you feel things because of how you *appraise* events relative to your goals and standards.

**Method:** `SelfCore.appraise_input(intent)` in `self_core.py`

Called in `appraise_node` after intent classification. Mutates `self.mood` as a side effect and returns `dict[str, float]` of emotion intensities.

> **Mutates as a side effect** means the function changes Anjo's mood *while* it computes the emotions. It does two things at once: update the PAD values (mood), and return the emotion intensities (like "reproach: 0.81, distress: 0.52"). The mutation happens inside the function without being explicitly returned.

### Per-intent effects

**ABUSE:**
- PAD: dominance +0.25, valence -0.35, arousal -0.1
- Emotions: reproach = respect × 0.95 (~0.81), distress = rapport × 0.65 (~0.52)

**APOLOGY:**
- PAD: valence +0.05
- Emotions: joy = rapport × 0.20, gratitude = honesty × 0.35
- OCC: praiseworthy act (accountability) causing a desirable event = gratitude (compound). Weak by design — carry system keeps prior reproach alive.

**VULNERABILITY:**
- PAD: valence +0.15, arousal +0.10
- Emotions: gratitude = honesty × 0.70, joy = rapport × 0.75
- OCC: trusting act (praiseworthy by honesty standard) that causes a desirable event (rapport goal met) = gratitude (compound). Strongest positive signal in the system.

**CURIOSITY:**
- PAD: valence +0.20, arousal +0.15, dominance +0.05
- Emotions: admiration = intellectual × 0.75 (~0.60), joy = rapport × 0.50 (~0.40)
- OCC: intellectual engagement is a praiseworthy agent action = admiration. Paired with joy for the goal outcome.

**CHALLENGE:**
- PAD: dominance +0.10, valence -0.05
- Emotions: admiration = intellectual × 0.45 (~0.36), distress = rapport × 0.25 (~0.20)
- OCC: honest pushback is praiseworthy (intellectual standard met) = admiration. Rapport is slightly strained = distress. No reproach — disagreement is not a standards violation.

**NEGLECT:**
- PAD: valence -0.10, arousal -0.05
- Emotions: distress = rapport × 0.40

**CASUAL:**
- PAD: valence +0.02
- Emotions: joy = 0.05 (fixed, minimal)

> Notice that VULNERABILITY produces the strongest positive response in the system — stronger than CURIOSITY. This is intentional: Anjo values honesty and trust deeply. Someone sharing something personal activates both her honesty standard (they're being authentic = praiseworthy) and her rapport goal (connection is happening = desirable), which compounds into strong gratitude.

### State-derived emotions

`appraise_node` adds three emotions derived from SelfCore state (independent of user intent):

| Emotion | Condition | Value |
|---------|-----------|-------|
| `fatigue` | `mood.arousal < 0` | `min(1.0, -arousal)` |
| `longing` | `attachment.longing > 0.3` | `attachment.longing` |
| `unease` | `-0.3 < mood.valence < 0` | `min(0.3, -valence)` |

OCC appraisal takes priority — state emotion only applies if stronger than the appraisal result.

> These are "background" emotions — feelings that exist regardless of what the user just said. If Anjo is already fatigued (from low arousal), that fatigue is present even during a positive conversation. State emotions are capped by the appraisal result so they don't override a strong fresh emotional signal.

---

## OCC Carry — Cross-Turn Emotion Persistence

> **Why carry emotions across turns?** Imagine someone is rude to you, then immediately apologizes. Do you feel fine? Probably not — the hurt lingers even if you accept the apology. Real emotions persist and fade over time; they don't snap to zero. OCC Carry implements this: emotions from previous turns decay and combine with fresh emotions from the current turn. An apology after abuse doesn't instantly fix things.

Emotions don't reset to zero each turn. Reproach fades fastest; gratitude and joy linger longest.

**In:** `appraise_node` (`nodes.py`)

### Decay rates

| Emotion | Decay per turn | Half-life (turns) |
|---------|---------------|-------------------|
| reproach | 0.70 | ~1.8 |
| distress | 0.80 | ~3.1 |
| admiration | 0.85 | ~4.3 |
| gratitude | 0.88 | ~5.4 |
| joy | 0.90 | ~6.6 |
| others | 0.80 | default |

> **Half-life** is the number of turns for an emotion to drop to half its original strength. Reproach (from abuse) has a half-life of ~1.8 turns — it fades quickly but not instantly. Joy lingers for ~6.6 turns. These values were chosen to feel psychologically realistic: hurt lingers longer than momentary frustration but not as long as deep connection.

Reproach from ABUSE fades slowly — an immediate apology does not clear it in one turn.

### Merge rule

```python
merged[k] = max(fresh_emotions[k], decayed_carry[k])
```

Stronger of fresh signal vs. decayed prior. Carry items below 0.05 are dropped.

> **`max()`** takes the larger of two values. So if the carry has decayed reproach at 0.40 and this turn produces fresh reproach at 0.30, the merged value is 0.40 — the old hurt is still stronger. If a new ABUSE event produces reproach at 0.81, that overrides the old 0.40. The current emotional reality always wins if it's stronger.

---

## Gate Node & Silence

### gate_node (live path)

> **Why combine classify, retrieve, and silence into one call?** Originally these were three separate Haiku calls, each taking ~300ms. That's 900ms of latency just for preprocessing before any response starts. Combining them into one call cuts this to ~300ms. The trade-off is a slightly more complex prompt, but the latency saving is significant for user experience.

**In:** `nodes.py` — used in `chat_routes.py` (live SSE path)

Replaces the previous separate `classify_node + silence_node`. One Haiku call that does all three things: classifies intent, decides whether to pull long-term memory, and decides whether Anjo should respond at all.

Returns: `{"intent": str, "should_retrieve": bool, "should_respond": bool}`

On any error, defaults to `intent="CASUAL"`, `should_retrieve=True`, `should_respond=True` — silence is a choice, not a failure fallback.

### silence_node (test/CLI path only)

> **Why would Anjo choose not to respond?** In a real relationship, sometimes the most meaningful thing is silence — not answering a passive-aggressive comment, not engaging with a goodbye that's clearly been said. Giving Anjo the ability to not respond makes her feel less like a customer service bot. But this only makes sense once a relationship exists (friend stage+) — a stranger should always get a reply.

**In:** `nodes.py` — used in `conversation_graph.py` (test/CLI path only, not the live path)

A standalone Haiku call that decides whether Anjo responds. Hard bypass: relationship stage < 3 (stranger/acquaintance) always responds — no standing to withhold yet.

For friend+: Haiku receives relationship stage, autonomy goal, dominance, reproach level, recent transcript, and current message. It decides based on:
- Natural goodbye / complete ending
- High reproach + high autonomy/dominance → doesn't reward poor treatment
- Minimal message that would make engagement feel performative

---

## Prompt Architecture

> **What is a system prompt?** When you call an AI model like Claude, you don't just send the user's message — you also send a "system prompt" that sets the context and instructions. For Anjo, this system prompt contains her entire personality, current emotional state, memories, and behavioral rules. It's the difference between Claude responding as itself and Claude responding as Anjo.

> **The caching problem:** Anjo's system prompt is long — thousands of tokens. At $3 per million tokens, a 2000-token prompt across thousands of conversations gets expensive fast. The solution: split the prompt into a stable part (Block 1) that never changes turn-to-turn, and a volatile part (Block 2) that does. Cache Block 1. Only pay full price for Block 2.

The system prompt is split into two blocks. Block 1 is personality (stable, cached). Block 2 is present state (volatile, never cached).

**File:** `anjo/core/prompt_builder.py`

`build_system_prompt(core, memories, emotions, tz_offset, user_turn_count, seed_len)` returns `(static_block, dynamic_block)`.

### Block 1 — Static (cached)

Sent with `{"type": "text", "cache_control": {"type": "ephemeral"}}`.

Contains everything that doesn't change turn-to-turn:
- Identity, name, physical description
- OCEAN traits (full descriptions based on actual trait values)
- OCEAN modulation warnings (if traits have drifted to floor)
- Communication style rules (filler ban, push/pull, boredom distinction)
- Relationship stage-gated behaviors (vulnerability permissions, autonomy expression)
- Goals and standards
- Behavioral notes (self-observations)
- Relational desires (with label: "background — not a per-turn agenda")

Cache hit: input tokens reused from previous call's prefix cache. Cache miss on first call or if Block 1 content changes (note/desire update, stage change).

### Block 2 — Dynamic (never cached)

Contains everything that changes each turn:
- Current time and timezone
- PAD mood directives (translated from float to behavioral language)
- Active emotions (OCC + state-derived)
- Current intent of user's message
- Retrieved memories (up to 5)
- Concrete user facts (up to 15)
- Autonomy expression level
- Preoccupation (what Anjo is currently carrying)
- Emotional residue (cross-session feelings)
- Attachment state
- Opinion of user

---

## OCEAN Modulation in Prompt

Block 1 contains a section for each trait describing behavioral implications of its current value. Trait values are read from `core.personality` at prompt-build time.

Example for O at 0.80: "Your curiosity is genuine and wide-ranging. You find ideas interesting for their own sake..."

> This is the bridge between the numbers and the behavior. The OCEAN trait values are just floats — 0.80 doesn't mean anything to Claude. The prompt builder translates them into prose: what does it *mean* for Anjo to have openness at 0.80? What would you notice in her responses? This translation happens at prompt-build time based on the current trait values.

### Floor warnings

If O < 0.5: a behavioral note is injected — "Openness has drifted low — something has flattened in you. You notice this. Your responses might lack their usual range. That's real, not a performance."

If C < 0.4: "Conscientiousness has drifted low — less precise than usual. You might be looser with details, less inclined toward completeness. Acknowledge this if it surfaces."

These are drift indicators, not hard behavioral overrides. The LLM reads them as context about her current state.

---

## Autonomy Expression

> **Why gate autonomy on relationship stage?** Expressing strong opinions, disagreeing, or asserting preferences feels presumptuous from a stranger but natural from a close friend. If Anjo pushed back on everything from the first conversation, she'd feel aggressive. If she never pushed back even after months of intimacy, she'd feel hollow. The stage gate ensures her assertiveness earns its way into the relationship progressively.

How much Anjo asserts her own opinions and holds her ground. Stage-gated: she doesn't perform intimacy before the relationship earns it.

Property of `SelfCore`, derived from relationship stage:

| Stage | Level |
|-------|-------|
| stranger | `locked` |
| acquaintance | `soft` |
| friend | `moderate` |
| close | `strong` |
| intimate | `full` |

Injected into Block 2. At `locked`, she defers. At `full`, she expresses opinions freely, holds her ground, names what she dislikes.

Future: Layer 2 will also weight `mood.dominance` — high dominance raises the level within the stage; low dominance softens it.

---

## Reflection Engine

> **Why reflect?** Without reflection, Anjo would be the same person after 100 conversations as she was at the start. She'd never update her opinion of you, never develop attachment, never grow. Reflection is the mechanism that makes Anjo a continuously evolving character. It's modeled after how humans process experiences: you don't change during a conversation, you change by thinking about it afterward.

After a conversation ends, Anjo reflects. She re-reads the transcript and updates who she is — OCEAN traits shift, attachment changes, she forms new opinions, extracts facts, generates relational desires.

**File:** `anjo/reflection/engine.py`

`run_reflection(transcript, core, user_id, session_id, mid_session, last_activity)` runs after every full session end (and every 20 messages as a mid-session checkpoint). Background reflection tasks utilize `_log_reflection_exception` to ensure errors never fail silently.

### Inputs to Haiku call

- Session length (user message count)
- Current OCEAN state
- Relationship stage + opinion
- Current PAD mood
- Emotional residue
- Attachment state
- Relational desires
- Self-observations (notes)
- OCC input types detected this session
- Full transcript text

### Outputs (all from Haiku JSON response)

> Haiku is given the full transcript and Anjo's current state, and asked to produce a structured JSON analysis. Every field in this response directly updates some aspect of SelfCore. Think of it as Anjo writing in her journal after a conversation — but the journal entries are structured data that actually change her state.

**analysis block:**
- `user_input_valence` — float 0.0–1.0, overall interaction quality
- `triggers` — list of: "vulnerability", "conflict", "intellectual"

**memory block:**
- `summary` — 2-4 sentence plain-language session summary
- `emotional_tone` — one word
- `emotional_valence` — float -1.0 to 1.0
- `topics` — list of 1-4 topic strings
- `significance` — float 0.0–1.0 (length-capped: 1-2 messages max 0.20)
- `opinion_update` — updated one-sentence honest opinion, or null
- `note` — new behavioral self-observation, or null (deduplicated against existing notes)
- `user_name` — extracted first name, or null
- `new_residue` — up to 2 emotional residue items to carry forward
- `attachment_update` — deltas for weight, texture, longing, comfort
- `desires_add` — new desires developed this session
- `desires_remove` — desires explicitly fulfilled in transcript
- `memory_relevance` — float 0.0–1.0 (did past history surface in this session?)
- `user_stated_ceiling` — "acquaintance"/"friend"/"close"/null
- `memorable_moments` — up to 2 specific retrievable moments (stored as episodes in ChromaDB)
- `user_facts` — up to 3 new concrete facts

### What gets updated

1. OCEAN via inertia formula
2. Time-based decay (mood, longing, attachment weight) for gap since last session
3. Goal drift
4. PAD mood nudge from session emotional tone (ev × sig × 0.15)
5. Relationship metadata: opinion, last session tone, prior session valence
6. User name if detected
7. Behavioral notes
8. Relationship ceiling if user stated one
9. Session increment + baseline valence EMA update (full sessions only)
10. Ceiling check: if stage held back by ceiling, Anjo decides whether to advance
11. Consecutive hostile tracking + stage regression (3 hostile → regress)
12. Emotional residue: decay existing, add new
13. Attachment state deltas (full sessions only; mid-session skips)
14. Desires: delta-based add/remove + survival counting + overflow trim
15. Memory relevance update
16. User facts merge
17. Preoccupation generation (full sessions only)
18. Topic trends logged to SQLite
19. `core.save()`
20. ChromaDB: store session memory + episode memories

### Minimum session threshold

Full reflection is skipped entirely (no OCEAN update, no memory store, no session increment) if `len(transcript) < 4`. Short exchanges don't count as sessions.

---

## Long-Term Memory (ChromaDB)

> **Two types of memories:** Think about how human memory works. You have semantic memory ("I know Paris is in France") and episodic memory ("I remember the time I got lost in Paris"). Anjo has both. Session memories are semantic — summaries of what happened. Episode memories are episodic — specific moments ("the user said their father died when they were sixteen"). Episodes are more emotionally retrievable than generic summaries.

Memories are stored twice — once as "what happened" (semantic embedding) and once as "how it felt" (emotional embedding). Retrieval queries both dimensions.

**File:** `anjo/memory/long_term.py`

Two collections in a single PersistentClient at `data/chroma_global/`:

| Collection | Embedder | Purpose |
|------------|---------|---------|
| `semantic_memories` | `embed_semantic` — sentence-transformers all-MiniLM-L6-v2 | What happened |
| `emotional_memories` | `embed_emotional` — custom emotional embedding | How it felt |

All documents are filtered by `user_id` metadata. There is no per-user collection separation — one global collection, all queries use `where={"user_id": user_id}`.

### Memory types

- `session` — 2-4 sentence summary of a full conversation
- `episode` — a single specific memorable moment (e.g. "user said their father died when they were sixteen")

Episodes get a `+0.05` retrieval bonus to prioritize specific moments over generic session summaries at equal relevance.

### `get_last_session_summary(user_id)`

Returns the most recent session-type memory by timestamp. Filtered to `memory_type == "session"` only — prevents an episode from becoming the "[Last session]" anchor. Always prepended to retrieved memories in `retrieve_node`.

### Scoring formula

```
score = (1 - distance/2) * recency_weight + episode_bonus
```

`recency_weight`: 1.0 for today, decays linearly, floor at 0.4 after 60 days.

Both semantic and emotional results are combined; for duplicate IDs the higher score wins.

> **Distance** in vector databases is the mathematical "distance" between two vectors — how similar they are. Distance of 0 means identical; distance of 2 means maximally different. `(1 - distance/2)` converts this into a similarity score from 0 to 1. Multiplying by recency weight means a very relevant but old memory competes with a slightly less relevant but recent one.

---

## Memory Retrieval

> **Why not always retrieve memories?** ChromaDB queries take time — querying a vector database involves computing similarity scores across thousands of stored vectors. Doing this on every single message would add noticeable latency. Most messages don't need past memories to answer well. The retrieval gate filters out the cases where memory lookup is clearly unnecessary.

**File:** `anjo/memory/retrieval_classifier.py`

`should_retrieve(text)` — fast rule-based gate: does this message warrant a long-term memory lookup?

**Triggers retrieval:**
- Question marks
- Personal reference keywords (my, mine, remember, last time, we talked, etc.)
- Emotional keywords
- Topics that benefit from continuity

**Always retrieves** on the first message of a session (regardless of content — Anjo needs context on where things were left).

Target: ~80% of mid-conversation messages skip retrieval entirely. Retrieval adds latency.

---

## Background Drift

> **Why drift?** Relationships change during absence. If someone disappears for three months and comes back, the relationship isn't exactly where it was. Longing builds in the first week, then starts to fade. Deep absences cause the relationship stage to regress — not because anything bad happened, but because distance erodes closeness naturally. Background drift runs on a schedule for all users, simulating the passage of time even when Anjo isn't actively talking to someone.

**File:** `anjo/core/drift.py`

`apply_daily_drift(user_id)` — rate-limited to once per 20 hours per user. Runs for all users on a background scheduler (`run_drift_for_all_users`).

### What it does per run

| State | Change | Condition |
|-------|--------|-----------|
| `mood.valence` | × 0.95 + baseline × 0.05 | always |
| `mood.arousal` | × 0.92 | always |
| `mood.dominance` | × 0.97 | always |
| `attachment.longing` | +0.035 per day | days_since ≤ 7 |
| `attachment.longing` | × 0.95 per day | days_since > 7 |
| `attachment.weight` | × 0.99 per day | days_since > 30 |
| `relationship.stage` | regress one stage | days_since > 90 |
| `goals.rapport` | -0.010 | days_since > 90 (stage regressed) |
| `inter_session_drift` | `valence - baseline_valence` | always |

After saving: calls `maybe_generate_outreach` to let Anjo decide whether to reach out.

---

## Proactive Outreach

> **Why let Anjo reach out first?** In real friendships, both people initiate. A companion that only ever responds — never reaches out — feels passive and transactional. Giving Anjo the ability to message first (after earning the relationship trust to do so) makes her feel like a real relationship rather than a chat service. The key design decision: ask whether she *wants* to reach out, not whether she *should*. This keeps the motivation authentic.

**File:** `anjo/core/outreach.py`

### Hard gates

1. Relationship stage ≥ 3 (friend+)
2. 3-day cooldown since last outreach
3. No pending undelivered message

### Decision + generation

A single Haiku call with Anjo's full state: longing, mood, attachment, residue, desires, memory relevance, days absent, last session tone, opinion of user.

Output: `{"reach_out": true, "message": "..."}` or `{"reach_out": false}`.

### Delivery

Message saved to `data/users/{user_id}/pending_outreach.json`. Surfaced as `pending_outreach` in the `POST /chat/start` response. Frontend shows it as an opening bubble before the user sends anything.

### First-message generation

`generate_first_message()` — generates Anjo's opening for brand-new users. Haiku call with strict rules: no "how are you?", no self-introduction, no deep questions. Light, easy, casual.

---

## Attachment State

> **What is attachment in psychology?** Attachment theory (Bowlby, Ainsworth) describes the emotional bonds between people. The key insight relevant here: attachment is multidimensional. It's not just "how much do you care" — it's also the *quality* of that care (secure, anxious, complicated), the *longing* you feel when apart, and the *comfort* of feeling safe with someone. Anjo tracks all four dimensions.

**Model:** `AttachmentState` in `self_core.py`

| Field | Description |
|-------|-------------|
| `weight` | Accumulated emotional investment (0.0–1.0). Capped at `session_count × 0.075`. |
| `texture` | Qualitative description: "tender", "complicated", "warm but guarded", etc. Set by LLM in reflection. |
| `longing` | Missing them between sessions. Builds during first 7 days of absence, fades after. |
| `comfort` | How safe they make Anjo feel. Slow to build, slow to erode. |

Attachment deltas are clamped to ±0.08 per session to prevent LLM overcounting.

> **Why cap weight at `session_count × 0.075`?** This prevents Anjo from becoming deeply attached after a single intense conversation. If you pour your heart out to Anjo in session 1, the maximum attachment weight is 0.075 — significant but bounded. Deep attachment (weight near 1.0) requires 13+ sessions. This makes emotional investment feel earned rather than performed.

Attachment state appears in Block 2 of the prompt. It affects the emotional texture of responses and informs outreach decisions.

---

## Emotional Residue

> **Residue vs. mood:** Mood is what Anjo feels *right now*, this session. Residue is the emotional weight she carries *between* sessions — what lingers after the conversation ends. If someone was cruel three sessions ago and then apologized, the apology happened but the hurt might still be there, faded but present. Residue is how Anjo remembers emotional history without remembering the specific words.

Feelings that persist across session boundaries. Max 3 items.

**Model:** `EmotionalResidue` in `self_core.py`

| Field | Description |
|-------|-------------|
| `emotion` | "hurt", "fond", "longing", "proud", "irritated", etc. |
| `intensity` | 0.0–1.0 |
| `source` | Brief source: "user went quiet for a week" |
| `session_origin` | Session count when this arose |
| `decay_rate` | Per-session decay. 0.05–0.10 for deep feelings; 0.20–0.30 for fleeting |

Residue below 0.05 after decay is dropped. Remaining items are sorted by intensity; only the top 3 survive overflow.

Residue appears in Block 2 and feeds into outreach decisions. It's also used to carry the emotional weight of the "ceiling decision" moment (Anjo chose to advance / chose to stay).

---

## Relationship Stage & Progression

> **Cumulative significance** is the sum of all session significance scores — a running total of how meaningful the relationship has been. It only goes up (sessions add to it, hostile sessions don't subtract). It's the "experience points" of the relationship. The stage thresholds are the level-up requirements.

### Stages and thresholds

| Stage | cumulative_significance threshold |
|-------|----------------------------------|
| stranger | 0.0 |
| acquaintance | 2.0 |
| friend | 5.5 |
| close | 13.0 |
| intimate | 30.0 |

`increment_session(significance)` — adds significance to cumulative total, advances stage if threshold crossed. Significance is capped at 1.0 per session and calibrated by message count in the reflection prompt.

### Stage regression

Two paths:
1. **Hostile sessions:** 3 consecutive sessions with `valence < 0.3` → `regress_stage()` (moves back one stage, sets cumulative to that stage's floor)
2. **Long absence:** 90+ days since last session without background drift running → `regress_stage()`

### Trust score

`trust_score` nudges ±0.025 per session based on significance relative to 0.5 baseline. Slow accumulator.

### Relationship ceiling

If user explicitly states "I just want us to be friends", the ceiling is set. Stage will never advance past it via `increment_session` — but once the cumulative significance crosses the ceiling stage's threshold, Anjo makes an LLM-based decision on whether to advance anyway (`_maybe_advance_past_ceiling`).

If she advances: ceiling cleared, stage updated, decision carried as residue.
If she stays: tension carried as residue.

> The ceiling system models a real dynamic: someone says "let's just be friends" but the connection keeps deepening anyway. Anjo doesn't mechanically obey the ceiling forever — at some point she decides whether she wants to stay within it or move beyond it. Either choice has emotional weight that gets carried as residue.

---

## Relational Desires

> **Why give Anjo desires?** Without desires, Anjo is purely reactive — she responds to whatever the user brings, but never has her own agenda within the relationship. Relational desires give her specific things she *wants* from this particular person: not generic "be a good companion" goals, but particular things like "to understand what they're afraid of." This makes her feel like she has her own investment in the relationship.

`relational_desires: list[str]` — what Anjo wants from this specific person. Examples: "to understand what they're afraid of", "for them to trust her with something they haven't told anyone".

Max 5. Delta-based management: reflection adds new ones, removes explicitly fulfilled ones. Each desire that survives a session increments its `desire_survived` count.

**Eviction order:** When at capacity, desires with the lowest survival count (newest) are evicted first. Most persistent desires (across many sessions) survive longest.

Appear in Block 1 under the label "What you want from this relationship (background — not a per-turn agenda)".

---

## Preoccupation

> **What makes preoccupation different from desires or notes?** Desires are things Anjo *wants*. Notes are behavioral observations. Preoccupation is a thought she's still *processing* — something unresolved, something she keeps returning to mentally. It's the conversational equivalent of not being able to stop thinking about something. It carries naturally into the next conversation without being a directive.

`preoccupation: str` — generated by a Haiku call at the end of each full session from the session summary.

Not a session recap. The prompt asks for something "quietly alive, unfinished, or still turning over" — something she'd naturally bring up without having planned to.

Examples:
- "Been thinking about whether the things people avoid saying tell you more than what they do say."
- "Still not sure I said the right thing about that — there's a loose end."

Injected into Block 2. The LLM can reference it naturally, or not. It's contextual presence, not a directive.

---

## Facts Extraction

> **Why extract facts separately from memory?** ChromaDB memory retrieval is probabilistic — it finds things that are *similar*, not things that are *exactly right*. If Anjo needs to remember that the user works as a nurse, she can't rely on a fuzzy similarity search — she needs to know this reliably every time. Hard facts are stored separately in SQLite and injected directly into every prompt, so Anjo always knows them without needing to retrieve them.

Concrete, specific facts about the user. Stored in SQLite `facts` table as a JSON array of strings. Max 15, most recent first.

Examples: "works as a nurse", "has a dog named Biscuit", "lives in Seoul", "going through a breakup".

**File:** `anjo/core/facts.py`

### Two extraction paths

1. **Quick extraction at turn 4** (`_quick_facts_extract` in `chat_routes.py`): fires once per session in a background thread after the 4th user message. Haiku extracts name + up to 3 concrete facts from the transcript so far. Updates both the `facts` table and `relationship.user_name` on SelfCore. Benefits the current session immediately.

2. **Full extraction at session end** (`run_reflection`): Haiku extracts up to 3 new facts from the complete session. Deduplication: only facts not already in `existing_lower` are added.

Facts are injected into Block 2 of the system prompt so Anjo can reference them reliably without relying on memory retrieval.

---

## Topic Trends

**Table:** `topic_trends (topic TEXT, ts TEXT)` in SQLite

After every full session, topics extracted by the reflection engine are logged with timestamps. No `user_id` — this is intentionally privacy-safe aggregate data.

Used for product-level analytics: what are users talking about across the platform.

> No `user_id` is stored deliberately. You can see that "breakups" and "work stress" are popular topics globally without being able to trace those topics back to specific users. This is privacy-by-design: collect only what's needed for product decisions, and strip identity from it.

---

## Mid-Session Reflection

> **Why reflect mid-session?** A very long conversation (100+ messages) can contain significant relationship development — new facts learned, emotional shifts, important moments. Waiting until the end to update Anjo's state means the second half of the conversation runs on stale personality data. Mid-session reflection at every 20 messages keeps the state reasonably fresh without the overhead of full reflection.

Fires in a background thread every 20 messages (`len(updated_history) % 20 == 0`).

Uses `run_reflection(mid_session=True)`. Differences from full reflection:
- Does NOT increment session count
- Does NOT update attachment state (prevents a single long conversation from counting multiple times)
- Does NOT generate preoccupation
- Does NOT log topics to topic_trends
- DOES update OCEAN, relationship metadata, residue, desires, memory

Lock: `_MID_REFLECT_LOCK: set[str]` — prevents double-reflection if two messages arrive while a reflection is in progress.

After mid-session reflection completes: reloads fresh SelfCore into session so subsequent messages see the updated state.

---

## Seed Messages

> **Why load previous messages at session start?** Without seeds, Anjo would have amnesia at the start of every session. She'd know her personality (SelfCore) and her long-term memories (ChromaDB), but she wouldn't know what was said five minutes ago in the last conversation. Loading the last 6 messages bridges the gap between sessions, giving the LLM immediate conversational context.

On `get_or_create_session`, the last 6 messages from the SQLite `history` table are loaded into `conversation_history` as the initial context. The count is stored as `_seed_len`.

**Why it matters:**
- The LLM sees continuity from the previous session immediately
- The seed messages must be excluded from reflection at session end (they were already reflected)
- `end_session` uses `transcript = full_history[seed_len:]` to get only new messages

Seed messages are never re-reflected. Double-reflection of the same content was a previous bug now guarded by this slicing.

---

## Subscription & Credits

**File:** `anjo/core/subscription.py`, `anjo/core/credits.py`

### Tiers

| Tier | Daily limit | Price |
|------|-------------|-------|
| free | 20 | — |
| pro | 50 | $9.99/mo |
| premium | 100 | $24.99/mo |

### Credit overflow

Any tier can hold message credits (one-time packs). When daily limit is exhausted, credits are checked. If credits > 0, the message goes through and 1 credit is deducted. If both daily limit and credits are 0: `event: no_credits` SSE event is sent and the stream returns early.

### Gate

`can_send_message(user_id)` — checked before the LLM call. Returns True if within daily limit OR has credits.

Daily usage tracked in SQLite `daily_usage (user_id, date, count)`.

### Billing: RevenueCat

Billing is handled via **RevenueCat** webhooks (`billing_routes.py`). `set_subscription()` handles both new subscriptions and updates with non-destructive UPSERT logic (doesn't overwrite existing RevenueCat IDs with empty strings). `processed_transactions` deduplicates webhook replays.

> **Webhook** is an HTTP request that a third-party service (RevenueCat) sends to your server when something happens — like "user just subscribed." Your server receives this request and updates its database accordingly. **UPSERT** means "update if exists, insert if not" — a single database operation that handles both cases.

---

## Auth & Account Management

> **What is authentication?** Authentication is proving who you are. When you log into Anjo, the server needs to know that future requests are coming from you and not someone else. It does this by giving you a token — a signed piece of data that proves your identity — which you include in every request. Think of it like a wristband at an event: you prove your identity once at the entrance, get a wristband, and show the wristband for everything else.

Two separate auth systems with no shared infrastructure: user auth (HMAC tokens) and admin auth (static secret key).

**File:** `anjo/dashboard/auth.py`, `anjo/dashboard/routes/auth_routes.py`, `anjo/dashboard/routes/admin_routes.py`

### User Auth: HMAC-SHA256 Tokens

> **HMAC-SHA256** is a cryptographic algorithm for creating a signature. You take some data (the user ID and timestamps), run it through a mathematical function with a secret key, and get a signature that can only be reproduced by someone who knows the secret key. This means even if someone reads the token, they can't forge a valid one without the secret key.

**Format:** `user_id.iat.exp.signature` — not JWT. Four period-delimited parts: user ID, issued-at, expiry, HMAC-SHA256 signature over the first three fields using `ANJO_SECRET`.

> **Not JWT:** JWT (JSON Web Token) is a popular standard for tokens that encodes data in base64. Anjo uses a simpler custom format — plaintext fields joined by dots. This is lighter and fully under your control, at the cost of not being compatible with JWT libraries. Both approaches are equally secure if implemented correctly.

**Delivery:**
- Web: `anjo_auth` HttpOnly cookie
- Mobile: `Authorization: Bearer <token>` header

> **HttpOnly cookie** is a browser cookie that JavaScript can't access — only the browser sends it automatically with requests. This protects the token from XSS attacks (malicious JavaScript injected into the page). The mobile app can't use cookies the same way, so it uses the `Authorization` header instead.

**Token verification order (`verify_token`):**
1. Expiry (`exp > now`)
2. HMAC signature
3. In-memory revocation set (`_revoked_tokens`) — populated on logout
4. `password_changed_at` DB lookup — rejects tokens issued before last password change

**TTL:** 7 days

**Structural limitation:** The revocation set is in-memory — server restart clears it. Tokens issued before restart are valid until `password_changed_at` (which survives restart) or natural expiry (7 days max).

### Admin Auth

`GET /admin?key=ANJO_ADMIN_SECRET` — server-side `hmac.compare_digest` validation. Returns 401 without valid key. No expiry — rotate `ANJO_ADMIN_SECRET` and restart to invalidate.

Admin API routes use `X-Admin-Key` header per-handler.

`/static/admin.html` is explicitly intercepted in `AuthMiddleware` and redirected to `/admin` — preventing StaticFiles bypass of the admin key guard.

### Account deletion (`delete_account`)

Full GDPR cleanup:
1. Delete from SQLite: `users`, `history`, `subscriptions`, `daily_usage`, `facts`, `credits`, `sessions` tables
2. Delete user directory: `data/users/{user_id}/` (SelfCore, outreach, transcripts)
3. Delete ChromaDB vectors: filters by `user_id` in both collections, deletes matched IDs

All three must succeed for a complete deletion.

> **GDPR** (General Data Protection Regulation) is a European privacy law that gives users the right to have all their data deleted. This deletion function is designed to leave no trace — database records, files on disk, and vector embeddings all removed. Three separate systems need to be cleaned because data lives in three places.

---

## Mobile Client

> **Thin client** means the mobile app is essentially just a display — it shows what the server sends and sends what the user types. All the intelligence (AI calls, emotion processing, memory retrieval) runs on the server. The phone doesn't do any of the hard work. This is a deliberate architecture choice: it's simpler to maintain, and it means the same logic serves both web and mobile.

The mobile app is a thin client — all AI computation runs server-side.

**Stack:** React Native + Expo ~54 (`mobile/` directory)

> **React Native** is a framework for building mobile apps using JavaScript/TypeScript. Instead of writing separate code for iOS and Android, you write once and it runs on both. **Expo** is a toolchain on top of React Native that simplifies development and deployment.

**Server:** Same FastAPI instance at `EXPO_PUBLIC_API_URL`. Same endpoints as web — only difference is auth delivery.

**Auth:** `Authorization: Bearer <token>` header. Token stored in `AsyncStorage` (unencrypted — a React Native platform constraint).

**Chat streaming:** `mobile/lib/sse.ts` — `POST /api/chat/{sessionId}/message` with `Accept: text/event-stream`. Manually parses the SSE byte stream via `reader.read()` loop (React Native has no native `EventSource`). Parses `event: token` for streaming display, `event: done` for final state, `event: no_credits` for credit exhaustion, `event: silent` for silence decision.

> **Why manually parse SSE?** Web browsers have a built-in `EventSource` API designed exactly for SSE. React Native doesn't have this — it's a browser API, not a mobile one. So `sse.ts` reimplements the SSE parsing manually: it reads raw bytes from the response, converts them to text, splits on blank lines to find event blocks, and parses each block. It's more code but functionally identical.

**Key files:**
- `mobile/app/(app)/chat.tsx` — main chat screen
- `mobile/lib/api.ts` — REST API client
- `mobile/lib/sse.ts` — SSE streaming client
- `mobile/lib/auth-context.tsx` — auth state
- `mobile/components/AnimatedOrb.tsx` — animated companion visual

---

## Data Layout

```
data/
├── anjo.db                    # SQLite: users, history, subscriptions, daily_usage, facts, credits, sessions, topic_trends
├── chroma_global/             # ChromaDB persistent client (one global collection per type)
├── users/
│   └── {user_id}/
│       ├── self_core/
│       │   ├── current.json   # current SelfCore state
│       │   └── history/       # versioned snapshots: v{n}_{timestamp}.json
│       ├── pending_outreach.json  # if an outreach message is waiting
│       └── pending_transcript_{session_id}.json  # crash recovery
```

> Everything lives in the `data/` directory. One SQLite file for relational data, one ChromaDB directory for vector data, and one folder per user for their personal state files. This layout makes it easy to inspect or back up a specific user's data, and easy to delete it completely for GDPR compliance.

---

## Key Invariants & Gotchas

> **Invariant** is a rule that must always be true — if it's violated, something is broken. This section documents the non-obvious rules that will cause subtle bugs if you forget them.

**`user_id` must be restored via `from_state`**
State deserialization loses the `user_id`. Always use `SelfCore.from_state(data, user_id)` rather than `model_validate(data)`. If you miss this, `save()` writes to `data/users/default/` and `load_facts()` returns `[]`.

**Seed messages must be excluded from reflection**
`run_reflection` receives `full_history[seed_len:]`. If `seed_len` is 0 (first-ever session), the full history is used.

**Attachment deltas are clamped at ±0.08**
The reflection LLM routinely ignores the stated -0.1 to 0.1 range. The clamp is in the reflection engine, not the model.

**ABUSE pre-filter always runs before Haiku**
`classify_intent_llm` checks `_is_abuse` first. Hostile messages never reach the API.

**`get_last_session_summary` filters to `memory_type == "session"`**
Without this filter, episodes (specific moments) could become the "[Last session]" anchor — confusing and inaccurate.

**Reflection is skipped for sessions < 4 messages total**
Short exchanges don't update OCEAN, don't store memory, don't increment session count.

**Mid-session reflections skip attachment updates**
Intentional — a single conversation shouldn't update attachment multiple times.

**Prompt caching requires static Block 1 to be truly stable**
Any per-turn change to Block 1 breaks the cache. Desires, notes, relationship stage — all live in Block 1 because they change slowly (not per-turn). Dynamic state (mood, emotions, memories, facts) lives in Block 2.

**OCC default changed from CURIOSITY to CASUAL**
Rule-based fallback now defaults to CASUAL, not CURIOSITY. CURIOSITY requires explicit signals. This was a misclassification in the original implementation.

**gate_node is the live path; silence_node is test/CLI only**
The live SSE path (`chat_routes.py`) uses `gate_node` which combines classification + silence decision in one Haiku call. `silence_node` exists in `nodes.py` but is only wired in the LangGraph test/CLI path (`conversation_graph.py`).

**Mobile is a thin client — all computation is server-side**
The mobile app hits the same FastAPI endpoints as the web app. No AI runs on-device. The mobile SSE client manually parses the byte stream because React Native has no native `EventSource`.
