"""SQLite database — per-thread WAL-mode connections with connection pool."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "anjo.db"

# Thread-local storage for per-thread connections
_thread_local = threading.local()

# Lock for initializing the database schema (only runs once)
_init_lock = threading.Lock()
_schema_initialized = False


def get_db() -> sqlite3.Connection:
    """Get a thread-local SQLite connection.

    Each thread gets its own connection. SQLite with WAL mode handles
    concurrent connections safely. Connections are reused within a thread
    and automatically closed when the thread exits.
    """
    if not hasattr(_thread_local, "conn") or _thread_local.conn is None:
        _thread_local.conn = _open(_DB_PATH)
    return _thread_local.conn


def _open(path: Path) -> sqlite3.Connection:
    global _schema_initialized
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")

    # Initialize schema exactly once
    with _init_lock:
        if not _schema_initialized:
            _init_schema(conn)
            _migrate_schema(conn)
            _schema_initialized = True

    return conn


def reset() -> None:
    """Close and forget the thread-local connection. Used in tests to swap DB paths."""
    if hasattr(_thread_local, "conn") and _thread_local.conn is not None:
        try:
            _thread_local.conn.close()
        except Exception:
            pass
        _thread_local.conn = None


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id            TEXT PRIMARY KEY,
            username           TEXT UNIQUE NOT NULL COLLATE NOCASE,
            email              TEXT UNIQUE NOT NULL,
            email_verified     INTEGER NOT NULL DEFAULT 0,
            hashed_password    TEXT NOT NULL,
            verification_token TEXT NOT NULL DEFAULT '',
            reset_token        TEXT NOT NULL DEFAULT '',
            reset_expiry       TEXT NOT NULL DEFAULT '',
            created_at         TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS credits (
            user_id             TEXT PRIMARY KEY,
            balance_usd         REAL    NOT NULL DEFAULT 0.0,
            total_spent_usd     REAL    NOT NULL DEFAULT 0.0,
            total_topped_up_usd REAL    NOT NULL DEFAULT 0.0,
            message_credits     INTEGER NOT NULL DEFAULT 0,
            last_updated        TEXT    NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id                 TEXT PRIMARY KEY,
            status                  TEXT NOT NULL DEFAULT 'none',
            tier                    TEXT NOT NULL DEFAULT 'free',
            paddle_customer_id      TEXT NOT NULL DEFAULT '',
            paddle_subscription_id  TEXT NOT NULL DEFAULT '',
            current_period_end      TEXT NOT NULL DEFAULT '',
            rollover_messages       INTEGER NOT NULL DEFAULT 0,
            updated_at              TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS daily_usage (
            user_id TEXT NOT NULL,
            date    TEXT NOT NULL,
            count   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, date)
        );

        CREATE TABLE IF NOT EXISTS facts (
            user_id    TEXT PRIMARY KEY,
            facts_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS letter_cache (
            user_id              TEXT PRIMARY KEY,
            letter               TEXT NOT NULL,
            generated_at         TEXT NOT NULL,
            trust_at_generation  REAL NOT NULL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            role       TEXT NOT NULL,
            content    TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, id);

        CREATE TABLE IF NOT EXISTS topic_trends (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            ts    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_topic_trends_ts ON topic_trends(ts);

        CREATE TABLE IF NOT EXISTS processed_transactions (
            transaction_id TEXT PRIMARY KEY,
            user_id        TEXT NOT NULL,
            processed_at   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS active_sessions (
            user_id      TEXT PRIMARY KEY,
            session_id   TEXT NOT NULL,
            state_json   TEXT NOT NULL,
            last_activity REAL NOT NULL,
            created_at   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memory_graph (
            id              TEXT PRIMARY KEY,
            user_id         TEXT NOT NULL,
            node_type       TEXT NOT NULL,
            content         TEXT NOT NULL,
            confidence      REAL NOT NULL DEFAULT 1.0,
            source_session  TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            superseded_at   TEXT,
            related_nodes   TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_memory_graph_user ON memory_graph(user_id, node_type);
        CREATE INDEX IF NOT EXISTS idx_memory_graph_active ON memory_graph(user_id, superseded_at);

        CREATE TABLE IF NOT EXISTS revoked_tokens (
            sig  TEXT    NOT NULL,
            exp  INTEGER NOT NULL,
            PRIMARY KEY (sig, exp)
        );
        CREATE INDEX IF NOT EXISTS idx_revoked_tokens_exp ON revoked_tokens(exp);
    """)


def _already_exists(exc: sqlite3.OperationalError) -> bool:
    """Return True only for 'already exists' migration errors that are safe to ignore."""
    msg = str(exc).lower()
    return "duplicate column name" in msg or "already exists" in msg


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply additive migrations to existing databases.

    Each ALTER TABLE is idempotent: OperationalError is swallowed only when the
    message confirms the column/index already exists. Any other OperationalError
    (disk full, corruption, permission denied) is re-raised.
    """
    try:
        conn.execute("ALTER TABLE users ADD COLUMN password_changed_at TEXT DEFAULT ''")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if not _already_exists(exc):
            raise

    try:
        # facts.confidence_json stores per-fact confidence scores as a JSON array of floats.
        # Each entry corresponds positionally to the fact in facts_json.
        # Default '[]' means all existing facts are treated as confidence=1.0.
        conn.execute("ALTER TABLE facts ADD COLUMN confidence_json TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if not _already_exists(exc):
            raise

    # Blind-index columns for encrypted lookups — HMAC-SHA256 of the plaintext value.
    # These allow equality WHERE queries without decrypting every row.
    try:
        conn.execute("ALTER TABLE subscriptions ADD COLUMN free_sessions_used INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if not _already_exists(exc):
            raise

    try:
        conn.execute("ALTER TABLE subscriptions ADD COLUMN rollover_messages INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError as exc:
        if not _already_exists(exc):
            raise

    for col in ("fs_account_id", "fs_subscription_id"):
        try:
            conn.execute(f"ALTER TABLE subscriptions ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError as exc:
            if not _already_exists(exc):
                raise

    for col in ("email_hmac", "reset_token_hmac", "verification_token_hmac"):
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError as exc:
            if not _already_exists(exc):
                raise

    for col in ("email_hmac", "reset_token_hmac", "verification_token_hmac"):
        try:
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_users_{col} ON users({col}) "
                f"WHERE {col} != ''"
            )
            conn.commit()
        except sqlite3.OperationalError as exc:
            if not _already_exists(exc):
                raise
