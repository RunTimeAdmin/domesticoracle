"""
Blast-radius circuit breaker.

Two overlapping caps prevent a runaway or injected agent from doing unlimited damage:

  Per-actor hourly limit   — after N guarded actions in a rolling hour window, that
                             actor's subsequent requests are forced to HOLD regardless
                             of policy verdict.  The owner can still approve each one;
                             the cap does not deny, it escalates.

  Global daily ceiling     — after M guarded actions across all actors in a calendar
                             day (UTC), *everything* is forced to HOLD until midnight.

Both limits are stored in SQLite so they survive restarts and are visible to all
workers.  Counts are pruned on startup to keep the table small.

Configure via environment variables:
    ORA_ACTOR_HOURLY_LIMIT   (default 20)
    ORA_DAILY_CAP            (default 100)
Set to 0 to disable a limit.
"""
import datetime
import os
import threading
import time

from db import connect as _connect

_lock = threading.Lock()

ACTOR_HOURLY_LIMIT = int(os.getenv("ORA_ACTOR_HOURLY_LIMIT", "20"))
DAILY_CAP          = int(os.getenv("ORA_DAILY_CAP", "100"))


def init_table() -> None:
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS actor_hourly (
            actor_id     TEXT    NOT NULL,
            window_start INTEGER NOT NULL,
            count        INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (actor_id, window_start)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_totals (
            date  TEXT PRIMARY KEY,
            count INTEGER NOT NULL DEFAULT 0
        )
    """)
    cutoff_hour = int(time.time() // 3600) * 3600 - 48 * 3600
    conn.execute("DELETE FROM actor_hourly WHERE window_start < ?", (cutoff_hour,))
    cutoff_day = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    conn.execute("DELETE FROM daily_totals WHERE date < ?", (cutoff_day,))
    conn.commit()


def check_and_record(actor_id: str) -> tuple[bool, str]:
    """Check limits and record the action if within bounds.

    Returns (True, "ok") if the action can proceed; (False, reason) if a limit
    would be exceeded — in which case the caller should force a HOLD.
    Limits of 0 are treated as disabled (unlimited).
    """
    now = time.time()
    hour_bucket = int(now // 3600) * 3600
    today = time.strftime("%Y-%m-%d", time.gmtime(now))

    with _lock:
        conn = _connect()

        if ACTOR_HOURLY_LIMIT > 0:
            row = conn.execute(
                "SELECT count FROM actor_hourly WHERE actor_id = ? AND window_start = ?",
                (actor_id, hour_bucket),
            ).fetchone()
            if row and row["count"] >= ACTOR_HOURLY_LIMIT:
                return False, (
                    f"Rate limit: {actor_id!r} has reached the "
                    f"{ACTOR_HOURLY_LIMIT}-action/hour cap. Action held for owner review."
                )

        if DAILY_CAP > 0:
            row = conn.execute(
                "SELECT count FROM daily_totals WHERE date = ?", (today,)
            ).fetchone()
            if row and row["count"] >= DAILY_CAP:
                return False, (
                    f"Daily cap reached: {DAILY_CAP} guarded actions today. "
                    "All further actions held until midnight UTC."
                )

        conn.execute(
            "INSERT INTO actor_hourly (actor_id, window_start, count) VALUES (?, ?, 1) "
            "ON CONFLICT (actor_id, window_start) DO UPDATE SET count = count + 1",
            (actor_id, hour_bucket),
        )
        conn.execute(
            "INSERT INTO daily_totals (date, count) VALUES (?, 1) "
            "ON CONFLICT (date) DO UPDATE SET count = count + 1",
            (today,),
        )
        conn.commit()

    return True, "ok"


def get_status() -> dict:
    """Return current rate-limit state for the Trust Center."""
    now = time.time()
    hour_bucket = int(now // 3600) * 3600
    today = time.strftime("%Y-%m-%d", time.gmtime(now))

    conn = _connect()
    rows = conn.execute(
        "SELECT actor_id, count FROM actor_hourly WHERE window_start = ? ORDER BY count DESC",
        (hour_bucket,),
    ).fetchall()
    actor_counts = {r["actor_id"]: r["count"] for r in rows}

    row = conn.execute(
        "SELECT count FROM daily_totals WHERE date = ?", (today,)
    ).fetchone()
    daily_count = row["count"] if row else 0

    return {
        "actor_hourly_limit": ACTOR_HOURLY_LIMIT,
        "daily_cap": DAILY_CAP,
        "today": today,
        "daily_count": daily_count,
        "daily_remaining": max(0, DAILY_CAP - daily_count) if DAILY_CAP > 0 else None,
        "current_hour_bucket": hour_bucket,
        "actor_counts_this_hour": actor_counts,
    }
