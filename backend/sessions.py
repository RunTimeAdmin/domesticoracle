"""
Durable owner session store.

Sessions are persisted to the shared SQLite DB (via db.connect()) so they survive
uvicorn restarts, --reload cycles, and are safe across multiple workers — each
worker hits the same DB file, so a session issued by worker A is visible to worker B.

Sliding-window expiry: every successful validation extends the TTL by SESSION_TTL,
keeping an active browser session alive indefinitely without a fixed logout.
"""
import secrets
import time

from db import connect as _connect

SESSION_TTL = 8 * 3600  # seconds; sliding window resets on each validated request


def init_table() -> None:
    """Create the sessions table if it doesn't exist. Called once at startup."""
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token      TEXT PRIMARY KEY,
            expires_at REAL NOT NULL
        )
    """)
    conn.commit()


def create() -> str:
    """Issue a new session. Returns the opaque hex token stored in the cookie."""
    token = secrets.token_hex(32)
    conn = _connect()
    conn.execute(
        "INSERT INTO sessions (token, expires_at) VALUES (?, ?)",
        (token, time.time() + SESSION_TTL),
    )
    conn.commit()
    return token


def validate(token: str | None) -> bool:
    """Return True if the token is known and unexpired. Extends the window on hit."""
    if not token:
        return False
    now = time.time()
    conn = _connect()
    row = conn.execute(
        "SELECT expires_at FROM sessions WHERE token = ?", (token,)
    ).fetchone()
    if row is None:
        return False
    if now > row["expires_at"]:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        return False
    conn.execute(
        "UPDATE sessions SET expires_at = ? WHERE token = ?",
        (now + SESSION_TTL, token),
    )
    conn.commit()
    return True


def revoke(token: str) -> None:
    """Immediately invalidate a session (logout)."""
    conn = _connect()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()


def prune() -> None:
    """Delete all expired sessions. Called at startup to keep the table tidy."""
    conn = _connect()
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
    conn.commit()
