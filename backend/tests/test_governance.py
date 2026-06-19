"""
Governance layer tests.

Three tests, each covering a distinct correctness contract:

  test_tamper_detection        — editing a signed ledger row fails verify_chain()
  test_owner_override          — owner approval bypasses policy re-evaluation
  test_backward_compat_legacy  — pre-patch rows (legacy canonical hash) still verify

Each test runs against an isolated SQLite database in a pytest tmp_path directory
and a freshly generated Ed25519 signing key injected via ORA_LEDGER_KEY. No
production keys or databases are touched.
"""
import hashlib
import json
import sqlite3
import time

import pytest

import crypto
import ledger
import policy
import consent


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_connect(db_path):
    """Return a _connect()-shaped callable wired to a temp SQLite file."""
    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    return _connect


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_key(monkeypatch):
    """Ephemeral Ed25519 key injected via env var; resets crypto module state."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, NoEncryption,
    )
    key = Ed25519PrivateKey.generate()
    key_hex = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()
    monkeypatch.setenv("ORA_LEDGER_KEY", key_hex)
    # Clear cached server key so the module reloads from ORA_LEDGER_KEY.
    monkeypatch.setattr(crypto, "_server_key", None)


@pytest.fixture()
def ledger_db(tmp_path, monkeypatch, fresh_key):
    """Isolated ledger-only DB. Yields the Path to the SQLite file."""
    db_file = tmp_path / "oracle.db"
    anchor_file = tmp_path / "anchor.log"

    connect = _make_connect(db_file)
    monkeypatch.setattr(ledger, "_connect", connect)
    monkeypatch.setattr(ledger, "ANCHOR_FILE", str(anchor_file))
    # Reset the incremental-verify checkpoint for this test.
    monkeypatch.setattr(ledger, "_verify_checkpoint",
                        {"id": 0, "hash": ledger.GENESIS_HASH})

    ledger.init_db()
    return db_file


@pytest.fixture()
def consent_db(tmp_path, monkeypatch, fresh_key):
    """Isolated DB for consent + ledger + policy. Yields the Path."""
    db_file = tmp_path / "oracle.db"
    anchor_file = tmp_path / "anchor.log"

    connect = _make_connect(db_file)
    monkeypatch.setattr(ledger,  "_connect", connect)
    monkeypatch.setattr(policy,  "_connect", connect)
    monkeypatch.setattr(consent, "_connect", connect)
    monkeypatch.setattr(ledger, "ANCHOR_FILE", str(anchor_file))
    monkeypatch.setattr(ledger, "_verify_checkpoint",
                        {"id": 0, "hash": ledger.GENESIS_HASH})
    # Clear in-process policy cache so seed defaults are read fresh.
    monkeypatch.setattr(policy, "_policy_cache", None)

    # Reset executor so tests never inherit a previous test's lambda.
    consent._executor = None

    # Initialises all tables: agents, approvals, nonces, policies, ledger.
    consent.init_db()
    return db_file


# ---------------------------------------------------------------------------
# Test 1 — Tamper detection
# ---------------------------------------------------------------------------

def test_tamper_detection(ledger_db):
    """Editing a signed field in any row causes verify_chain() to fail at that row.

    The chain links each entry's hash to its predecessor. Changing any signed
    field breaks the hash at that entry, and every subsequent entry's prev_hash
    then also mismatches — but we only need to flag the earliest broken link.
    """
    e1 = ledger.append("ora.core", "search_web",  "query=weather", "allow", "executed")
    e2 = ledger.append("ora.core", "control_device", "entity=light", "allow", "executed")  # noqa: F841

    # Baseline: chain must be intact before any tampering.
    pre = ledger.verify_chain(full=True)
    assert pre["valid"], f"Chain should be intact before tamper; got {pre}"
    assert pre["checked"] == 2

    # Tamper: overwrite args_summary on the first entry via direct SQL.
    # This is exactly what an attacker with DB write access would try — change
    # the payload without being able to recompute the Ed25519 signature.
    conn = sqlite3.connect(str(ledger_db))
    conn.execute(
        "UPDATE ledger SET args_summary = 'INJECTED' WHERE id = ?", (e1["id"],)
    )
    conn.commit()
    conn.close()

    # Verify: chain must be invalid and the break must be pinpointed at e1.
    # full=True ensures we rescan from genesis rather than relying on the
    # incremental checkpoint that was updated by the pre-tamper call above.
    post = ledger.verify_chain(full=True)
    assert not post["valid"], "Chain should be invalid after args_summary was changed"
    assert post["broken_at"] == e1["id"], (
        f"Break should be at #{e1['id']} (the tampered row); "
        f"got broken_at={post['broken_at']}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Owner approval overrides policy re-evaluation
# ---------------------------------------------------------------------------

def test_owner_override(consent_db):
    """Owner approval executes the held action even if policy changed to DENY.

    The previous implementation re-evaluated policy at approval time, making
    'Approve' misleading: a new DENY rule added between hold and approval
    could silently block an action the owner had explicitly cleared.

    The current implementation treats owner approval as a root-authority
    override. Policy governs autonomous action; the owner overrides it.
    """
    executed: list[tuple] = []

    def _executor(action: str, args: dict) -> str:
        executed.append((action, dict(args)))
        return f"ok:{action}"

    consent.set_executor(_executor)

    # Add a spend_limit policy that HOLDs any purchase (max_amount=0 ⇒ $0.01 trips it).
    policy.add_policy("spend_limit", {"max_amount": 0}, label="Hold all purchases")

    # Issue the action through the gate — it must be held, not executed.
    gate_result = consent.request_action(
        actor_id="oracle.agent",
        action="make_purchase",
        args={"item": "notebook", "amount": 12.50},
        execute=lambda: pytest.fail("execute() must not be called on HOLD"),
    )
    assert gate_result["status"] == "held", (
        f"Expected 'held', got '{gate_result['status']}'"
    )
    approval_id = gate_result["approval_id"]
    assert approval_id is not None

    # Now add a DENY policy for the same action — simulating a policy change
    # between the hold and the owner's decision.
    policy.add_policy("action_deny", {"action": "make_purchase"},
                      label="Block all purchases")

    # Owner approves. Must execute despite the new DENY policy.
    resolved = consent.resolve(approval_id, "approve")
    assert resolved.get("ok"), (
        f"resolve() returned error: {resolved.get('error')}"
    )
    assert resolved["status"] == "executed"
    assert len(executed) == 1, "Executor must be called exactly once"
    assert executed[0][0] == "make_purchase"

    # Ledger must record the resolution with decision="allowed (owner)".
    entries = ledger.list_entries()
    resolution_entries = [e for e in entries if e["action"] == "resolve"]
    assert len(resolution_entries) == 1, "Expected exactly one resolution entry"
    assert resolution_entries[0]["decision"] == "allowed (owner)", (
        f"Expected decision='allowed (owner)'; got '{resolution_entries[0]['decision']}'"
    )
    assert resolution_entries[0]["status"] == "executed"

    # approved_by_owner column must be set.
    conn = sqlite3.connect(str(consent_db))
    row = conn.execute(
        "SELECT approved_by_owner FROM approvals WHERE id = ?", (approval_id,)
    ).fetchone()
    conn.close()
    assert row is not None, "Approval row not found"
    assert row[0] == 1, (
        f"approved_by_owner should be 1 after owner approval; got {row[0]}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Backward compatibility: legacy canonical hash rows still verify
# ---------------------------------------------------------------------------

def test_backward_compat_legacy_hash(ledger_db):
    """Rows written with the pre-patch canonical hash format still pass verify_chain().

    The governance patch expanded the signed payload to include args_json,
    category, and risk. Rows written before the patch use a smaller 8-field
    payload. verify_chain() must accept both via _canonical_legacy() fallback.

    This test simulates an existing ledger upgraded in-place: one legacy row
    already in the DB, followed by a new row appended via the patched code.
    The chain across the format boundary must remain valid.
    """
    ts = time.time()

    # Build and hash a row using the OLD canonical format (8 fields, no
    # args_json/category/risk). This is what ledger.py wrote before the patch.
    legacy_fields = {
        "ts":          ts,
        "actor_id":    "ora.core",
        "action":      "control_device",
        "args_summary": "entity=light.living_room, command=on",
        "decision":    "allow",
        "status":      "executed",
        "outcome":     "Living Room Lamp is now on.",
        "prev_hash":   ledger.GENESIS_HASH,
    }
    legacy_canonical = json.dumps(legacy_fields, sort_keys=True, separators=(",", ":"))
    legacy_hash = hashlib.sha256(legacy_canonical.encode("utf-8")).hexdigest()
    legacy_sig   = crypto.sign(legacy_hash.encode("utf-8"))

    # Insert directly — bypassing ledger.append(), which writes the new format.
    # args_json='{}' (the migration default), category='', risk=0.
    conn = sqlite3.connect(str(ledger_db))
    conn.execute(
        """INSERT INTO ledger
               (ts, actor_id, action, args_summary, args_json, decision, status,
                outcome, prev_hash, hash, sig, category, risk)
           VALUES (?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, '', 0)""",
        (
            ts,
            "ora.core",
            "control_device",
            "entity=light.living_room, command=on",
            "allow",
            "executed",
            "Living Room Lamp is now on.",
            ledger.GENESIS_HASH,
            legacy_hash,
            legacy_sig,
        ),
    )
    conn.commit()
    conn.close()

    # The legacy row alone must verify via the _canonical_legacy() fallback.
    result = ledger.verify_chain(full=True)
    assert result["valid"], (
        f"Legacy-format row failed verification: {result['reason']}"
    )
    assert result["checked"] == 1

    # Append a new row using the current code. It will use the new canonical
    # format and prev_hash = legacy_hash.
    ledger.append("ora.core", "search_web", "query=news", "allow", "executed")

    # The full chain — legacy row + new row — must verify.
    result2 = ledger.verify_chain(full=True)
    assert result2["valid"], (
        f"Chain broke after new-format row was appended: {result2['reason']}"
    )
    assert result2["checked"] == 2, (
        f"Expected 2 rows checked; got {result2['checked']}"
    )
