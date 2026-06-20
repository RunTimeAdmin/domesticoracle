"""
External anchor integrity tests.

The anchor file is the third layer of defence: it catches the one thing that
hash chains and Ed25519 signatures alone cannot — wholesale rollback or
truncation of the ledger. After each append, the new chain head (id + hash)
is written to an append-only file outside the database with its own signature.

Four scenarios verified:
  no_anchor_yet      — anchored=False, consistent=True (nothing to compare)
  normal_match       — after a normal append, anchor matches DB head
  rollback_detected  — entry recorded in anchor but deleted from DB
  rewrite_detected   — entry in DB but hash was tampered after anchoring

All tests run against an isolated DB/anchor pair so the real oracle.db and
production anchor file are never touched.
"""
import json
import sqlite3

import pytest

import ledger


def _make_connect(db_path):
    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    return _connect


@pytest.fixture()
def anchor_db(tmp_path, monkeypatch, fresh_key):
    """Isolated ledger DB + anchor file."""
    db_file    = tmp_path / "oracle.db"
    anchor_log = tmp_path / "anchor.log"

    connect = _make_connect(db_file)
    monkeypatch.setattr(ledger, "_connect", connect)
    monkeypatch.setattr(ledger, "ANCHOR_FILE", str(anchor_log))
    monkeypatch.setattr(ledger, "_verify_checkpoint", {"id": 0, "hash": ledger.GENESIS_HASH})
    ledger.init_db()

    yield db_file, anchor_log


# ── no anchor yet ─────────────────────────────────────────────────────────────

def test_no_anchor_file_is_consistent(tmp_path, monkeypatch, fresh_key):
    """When no anchor file exists, verify_anchor reports anchored=False, consistent=True."""
    db_file = tmp_path / "oracle.db"
    monkeypatch.setattr(ledger, "_connect", _make_connect(db_file))
    monkeypatch.setattr(ledger, "ANCHOR_FILE", str(tmp_path / "does_not_exist.log"))
    monkeypatch.setattr(ledger, "_verify_checkpoint", {"id": 0, "hash": ledger.GENESIS_HASH})
    ledger.init_db()

    result = ledger.verify_anchor()
    assert result["anchored"] is False
    assert result["consistent"] is True


# ── normal case ───────────────────────────────────────────────────────────────

def test_anchor_matches_chain_after_normal_append(anchor_db):
    """After normal appends, verify_anchor reports anchored=True, consistent=True."""
    db_file, _ = anchor_db
    ledger.append("ora.core", "test", "a=1", "allow", "executed")
    ledger.append("ora.core", "test", "a=2", "allow", "executed")

    result = ledger.verify_anchor()
    assert result["anchored"] is True
    assert result["consistent"] is True, f"Anchor should match DB: {result['reason']}"


# ── rollback/truncation ───────────────────────────────────────────────────────

def test_anchor_detects_rollback(anchor_db):
    """Entry present in anchor file but deleted from DB → rollback detected.

    This is the threat scenario anchor files exist to catch: the database is
    wiped/truncated (or replaced with an older backup) while the anchor file
    on separate storage still records the most recently anchored head.
    verify_anchor() reads the last anchor line and checks whether that entry
    still exists in the DB; a missing entry signals truncation.
    """
    db_file, anchor_log = anchor_db
    e1 = ledger.append("ora.core", "test", "a=1", "allow", "executed")
    e2 = ledger.append("ora.core", "test", "a=2", "allow", "executed")
    e3 = ledger.append("ora.core", "test", "a=3", "allow", "executed")

    # Anchor file now records entry 3 as the last head.
    # Simulate truncation: delete the last two entries from the DB.
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute("DELETE FROM ledger WHERE id IN (?, ?)", (e2["id"], e3["id"]))
        conn.commit()

    result = ledger.verify_anchor()
    assert result["anchored"] is True
    assert result["consistent"] is False, "Rollback must be detected"
    assert "missing" in result["reason"].lower(), (
        f"Reason should mention 'missing'; got: {result['reason']!r}"
    )


def test_anchor_detects_history_rewrite(anchor_db):
    """Anchored entry exists in DB but with a different hash → history rewrite detected.

    This catches the scenario where the DB was rebuilt from scratch (producing
    internally consistent hashes) but cannot reproduce the exact bits that were
    anchored at the original timestamp.
    """
    db_file, anchor_log = anchor_db
    e1 = ledger.append("ora.core", "test", "a=1", "allow", "executed")
    e2 = ledger.append("ora.core", "test", "a=2", "allow", "executed")

    # Tamper with e2's hash in the DB (simulating a rebuilt chain where the
    # attacker recomputed hashes from a modified payload).
    forged_hash = "b" * 64
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute("UPDATE ledger SET hash = ? WHERE id = ?", (forged_hash, e2["id"]))
        conn.commit()

    result = ledger.verify_anchor()
    assert result["anchored"] is True
    assert result["consistent"] is False, "History rewrite must be detected"
    reason_lower = result["reason"].lower()
    assert "hash" in reason_lower or "differ" in reason_lower, (
        f"Reason should mention hash difference; got: {result['reason']!r}"
    )
