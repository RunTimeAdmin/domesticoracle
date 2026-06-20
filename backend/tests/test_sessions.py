"""
sessions.py unit tests.

Contracts locked in here (no HTTP, no app import):

  create()   — issues a unique 64-char hex token persisted in the DB
  validate() — True for fresh; extends TTL (sliding window); False for
               None / unknown / expired; expired rows are deleted on check
  revoke()   — immediately deletes the row; subsequent validate returns False
  prune()    — deletes all expired rows, leaves unexpired ones intact

All tests run against an isolated SQLite DB in pytest tmp_path.  The shared
db module is redirected via monkeypatching so oracle.db is never touched.
"""
import sqlite3
import threading
import time

import pytest

import db as _db
import sessions


@pytest.fixture()
def session_db(tmp_path, monkeypatch):
    """Isolated DB for session unit tests.

    Replaces db._local with a fresh threading.local() so no cached connection
    from other tests bleeds in, and patches db.DB_PATH to the tmp DB file.
    """
    db_file = tmp_path / "oracle.db"
    monkeypatch.setattr(_db, "DB_PATH",  str(db_file))
    monkeypatch.setattr(_db, "_local",   threading.local())
    sessions.init_table()
    yield db_file


def _flush(db_file):
    """Evict the thread-local cached connection so the next call reconnects.

    Required after writing to the DB via a direct sqlite3.connect() that
    bypasses the thread-local cache, so the module's own calls see the changes.
    """
    if hasattr(_db._local, "conn"):
        delattr(_db._local, "conn")


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------

def test_create_returns_valid_hex_token(session_db):
    token = sessions.create()
    assert isinstance(token, str)
    assert len(token) == 64         # secrets.token_hex(32) → 64 hex chars
    bytes.fromhex(token)            # must be valid hex, not just a string


def test_create_tokens_are_unique(session_db):
    tokens = {sessions.create() for _ in range(10)}
    assert len(tokens) == 10        # all distinct


def test_create_persists_to_db(session_db):
    token = sessions.create()
    _flush(session_db)
    with sqlite3.connect(str(session_db)) as conn:
        row = conn.execute(
            "SELECT expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
    assert row is not None, "create() must write the token to the DB"
    assert row[0] > time.time(), "expires_at must be in the future"


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------

def test_validate_fresh_session_returns_true(session_db):
    token = sessions.create()
    assert sessions.validate(token) is True


def test_validate_none_returns_false(session_db):
    assert sessions.validate(None) is False


def test_validate_empty_string_returns_false(session_db):
    assert sessions.validate("") is False


def test_validate_unknown_token_returns_false(session_db):
    assert sessions.validate("a" * 64) is False


def test_validate_expired_token_returns_false(session_db):
    """An expired session is rejected and deleted from the DB on check."""
    token = sessions.create()
    # Manually backdate the expiry so it's already past.
    with sqlite3.connect(str(session_db)) as conn:
        conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE token = ?",
            (time.time() - 1, token),
        )
        conn.commit()
    _flush(session_db)

    assert sessions.validate(token) is False

    # The row must have been pruned during the failed validation.
    _flush(session_db)
    with sqlite3.connect(str(session_db)) as conn:
        row = conn.execute(
            "SELECT 1 FROM sessions WHERE token = ?", (token,)
        ).fetchone()
    assert row is None, "Expired session must be deleted on validation failure"


def test_validate_extends_ttl(session_db):
    """Each successful validation pushes the expiry forward (sliding window)."""
    token = sessions.create()
    _flush(session_db)
    with sqlite3.connect(str(session_db)) as conn:
        before = conn.execute(
            "SELECT expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()[0]
    _flush(session_db)

    time.sleep(0.05)
    assert sessions.validate(token) is True

    _flush(session_db)
    with sqlite3.connect(str(session_db)) as conn:
        after = conn.execute(
            "SELECT expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()[0]

    assert after > before, "validate() must extend the expiry (sliding window)"


# ---------------------------------------------------------------------------
# revoke()
# ---------------------------------------------------------------------------

def test_revoke_invalidates_session(session_db):
    token = sessions.create()
    assert sessions.validate(token) is True
    sessions.revoke(token)
    assert sessions.validate(token) is False


def test_revoke_nonexistent_token_is_noop(session_db):
    """Revoking a token that was never issued must not raise."""
    sessions.revoke("b" * 64)   # should complete without error


# ---------------------------------------------------------------------------
# prune()
# ---------------------------------------------------------------------------

def test_prune_removes_expired_keeps_valid(session_db):
    valid   = sessions.create()
    expired = sessions.create()

    with sqlite3.connect(str(session_db)) as conn:
        conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE token = ?",
            (time.time() - 1, expired),
        )
        conn.commit()
    _flush(session_db)

    sessions.prune()

    assert sessions.validate(valid)          is True
    assert sessions.validate(expired)        is False
