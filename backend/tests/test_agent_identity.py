"""
Agent identity tests: revocation and external-agent request signing.

Five contracts:
  revoked_actor_denied        — revoked actor is blocked at the gate before policy runs
  revoked_before_policy       — revocation check precedes policy eval (order of operations)
  valid_signed_request        — well-formed Ed25519 request from external agent passes
  wrong_signature_rejected    — request signed with a different key is rejected
  replay_rejected             — second call with the same nonce is rejected
  stale_timestamp_rejected    — request with ts > REQUEST_TTL_SECONDS in the past fails

All tests run against an isolated SQLite database; crypto._DB_PATH is patched
so nonce persistence uses the test DB, not oracle.db in production.
"""
import json
import secrets
import sqlite3
import time

import pytest

import consent
import crypto
import ledger
import limits
import policy


def _make_connect(db_path):
    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    return _connect


@pytest.fixture()
def identity_db(tmp_path, monkeypatch, fresh_key):
    """Isolated DB with all tables; crypto._DB_PATH points to the test DB so
    consume_nonce() writes nonces there instead of the real oracle.db."""
    db_file    = tmp_path / "oracle.db"
    anchor_log = tmp_path / "anchor.log"

    connect = _make_connect(db_file)
    monkeypatch.setattr(ledger,   "_connect", connect)
    monkeypatch.setattr(policy,   "_connect", connect)
    monkeypatch.setattr(consent,  "_connect", connect)
    monkeypatch.setattr(limits,   "_connect", connect)
    monkeypatch.setattr(crypto,   "_DB_PATH", str(db_file))
    monkeypatch.setattr(ledger,   "ANCHOR_FILE", str(anchor_log))
    monkeypatch.setattr(ledger,   "_verify_checkpoint", {"id": 0, "hash": ledger.GENESIS_HASH})
    monkeypatch.setattr(policy,   "_policy_cache", None)
    consent._executor = None
    consent.init_db()

    yield db_file


def _disable_rate_limits(monkeypatch):
    monkeypatch.setattr(limits, "ACTOR_HOURLY_LIMIT", 0)
    monkeypatch.setattr(limits, "DAILY_CAP", 0)


# ── revocation ────────────────────────────────────────────────────────────────

def test_revoked_actor_denied_at_gate(identity_db, monkeypatch):
    """A revoked actor is denied immediately and the executor is never called."""
    _disable_rate_limits(monkeypatch)

    consent.set_agent_status("ora.shopping", "revoked")

    executed = []
    result = consent.request_action(
        actor_id="ora.shopping",
        action="make_purchase",
        args={"item": "book", "amount": 5.0},
        execute=lambda: executed.append(True) or "ok",
    )

    assert result["status"] == "denied", f"Revoked actor must be denied; got {result}"
    assert "revoked" in result["reason"].lower()
    assert not executed
    assert result["ledger_id"] is not None, "Revoked attempt must still appear in the ledger"


def test_revoked_actor_denied_before_policy_runs(identity_db, monkeypatch):
    """Revocation check precedes policy evaluation.

    A permissive policy (no restricting rules) would normally ALLOW the action.
    If revocation happened AFTER policy eval, the action would slip through.
    This test confirms the gate rejects the actor even with a clean policy slate.
    """
    _disable_rate_limits(monkeypatch)

    # Clear all policies — without revocation enforcement, this actor would pass.
    with sqlite3.connect(str(identity_db)) as conn:
        conn.execute("DELETE FROM policies")
        conn.commit()
    monkeypatch.setattr(policy, "_policy_cache", None)

    consent.set_agent_status("ora.core", "revoked")

    executed = []
    result = consent.request_action(
        actor_id="ora.core",
        action="search_web",
        args={"query": "test"},
        execute=lambda: executed.append(True) or "ok",
    )

    assert result["status"] == "denied"
    assert not executed


def test_restore_revoked_actor_allows_again(identity_db, monkeypatch):
    """Re-activating a revoked actor restores normal gate processing."""
    _disable_rate_limits(monkeypatch)

    with sqlite3.connect(str(identity_db)) as conn:
        conn.execute("DELETE FROM policies")
        conn.commit()
    monkeypatch.setattr(policy, "_policy_cache", None)

    consent.set_agent_status("ora.home", "revoked")

    r1 = consent.request_action("ora.home", "control_device", {"entity": "light"}, lambda: "ok")
    assert r1["status"] == "denied"

    consent.set_agent_status("ora.home", "active")
    executed = []
    r2 = consent.request_action(
        "ora.home", "control_device", {"entity": "light"},
        lambda: executed.append(True) or "ok",
    )
    assert r2["status"] == "executed"
    assert executed


# ── external agent signing ────────────────────────────────────────────────────

def _sign_request(priv_hex: str, actor_id: str, action: str,
                  args: dict, nonce: str, ts: float) -> str:
    """Sign a request with the agent's private key; return sig hex."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
    args_canonical = json.dumps(args or {}, sort_keys=True, separators=(",", ":"))
    message = crypto.agent_request_message(actor_id, action, args_canonical, nonce, ts)
    return priv.sign(message).hex()


def test_valid_signed_request_passes(identity_db):
    agent   = consent.register_agent("test_bot")
    nonce   = secrets.token_hex(16)
    ts      = time.time()
    action  = "search_web"
    args    = {"query": "weather"}
    sig     = _sign_request(agent["private_key"], agent["id"], action, args, nonce, ts)

    ok, reason = consent.verify_agent_request(agent["id"], action, args, nonce, ts, sig)
    assert ok, f"Valid signed request must pass; reason={reason!r}"
    assert reason == "ok"


def test_wrong_signature_rejected(identity_db):
    """Signature from a different key must be rejected."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    agent   = consent.register_agent("sig_test_bot")
    nonce   = secrets.token_hex(16)
    ts      = time.time()
    action  = "search_web"
    args    = {"query": "test"}

    # Sign with a freshly generated key that is not the registered agent key.
    wrong_key = Ed25519PrivateKey.generate()
    args_canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
    message    = crypto.agent_request_message(agent["id"], action, args_canonical, nonce, ts)
    bad_sig    = wrong_key.sign(message).hex()

    ok, reason = consent.verify_agent_request(agent["id"], action, args, nonce, ts, bad_sig)
    assert not ok, "Wrong-key signature must be rejected"
    assert "signature" in reason.lower()


def test_replay_rejected(identity_db):
    """A nonce may only be used once — second call with the same nonce is blocked."""
    agent  = consent.register_agent("replay_bot")
    nonce  = secrets.token_hex(16)
    ts     = time.time()
    action = "search_web"
    args   = {"query": "test"}
    sig    = _sign_request(agent["private_key"], agent["id"], action, args, nonce, ts)

    ok1, _ = consent.verify_agent_request(agent["id"], action, args, nonce, ts, sig)
    assert ok1, "First call must succeed"

    ok2, reason2 = consent.verify_agent_request(agent["id"], action, args, nonce, ts, sig)
    assert not ok2, "Replay with the same nonce must be rejected"
    assert "nonce" in reason2.lower() or "replay" in reason2.lower()


def test_stale_timestamp_rejected(identity_db):
    """A request timestamped more than REQUEST_TTL_SECONDS in the past is rejected."""
    agent  = consent.register_agent("stale_bot")
    nonce  = secrets.token_hex(16)
    ts     = time.time() - (crypto.REQUEST_TTL_SECONDS + 10)   # definitively stale
    action = "search_web"
    args   = {}
    sig    = _sign_request(agent["private_key"], agent["id"], action, args, nonce, ts)

    ok, reason = consent.verify_agent_request(agent["id"], action, args, nonce, ts, sig)
    assert not ok, "Stale timestamp must be rejected"
    assert "freshness" in reason.lower() or "timestamp" in reason.lower()


def test_owner_denial_resolves_held_action(identity_db, monkeypatch):
    """resolve(approval_id, 'deny') closes the approval as 'denied' without calling executor."""
    _disable_rate_limits(monkeypatch)

    def _executor(action, args):
        pytest.fail("executor must not run when owner denies")

    consent.set_executor(_executor)
    policy.add_policy("spend_limit", {"max_amount": 0}, label="Hold all purchases")

    gate = consent.request_action(
        actor_id="ora.core",
        action="make_purchase",
        args={"item": "expensive_item", "amount": 999.0},
        execute=lambda: pytest.fail("execute() must not run"),
    )
    assert gate["status"] == "held"

    resolved = consent.resolve(gate["approval_id"], "deny")
    assert resolved.get("ok"), f"resolve() returned error: {resolved.get('error')}"
    assert resolved["status"] == "denied"


def test_resolve_nonexistent_approval_returns_error(identity_db):
    """Resolving an approval ID that does not exist returns an error dict."""
    result = consent.resolve("apr_doesnotexist", "approve")
    assert result.get("ok") is False
    assert "error" in result
