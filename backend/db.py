"""
Shared SQLite connection: one persistent per-thread connection with WAL mode
and sane pragmas applied once at creation time.

WAL (Write-Ahead Log) lets readers proceed concurrently with a write, which
matters for the Trust Center: ledger reads no longer stall behind action
writes. busy_timeout prevents spurious "database is locked" errors under
contention. synchronous=NORMAL is the standard WAL durability trade-off and
is appropriate for this app.
"""
import os
import sqlite3
import threading

_DATA_DIR = os.environ.get("ORA_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_DATA_DIR, "oracle.db")
_local = threading.local()


def connect() -> sqlite3.Connection:
    """Return the per-thread SQLite connection, creating and configuring it
    on the first call for each thread.

    Re-using the connection avoids per-call open/close overhead (dozens of
    round-trips per guarded action under the old code). The thread-local
    pattern is safe with FastAPI's async+threadpool model because SQLite
    connections must not be shared across threads.
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn
