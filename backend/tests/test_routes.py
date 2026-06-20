"""
FastAPI route-layer tests.

These are the only tests that exercise the seam unit tests cannot reach:
the HTTP transport, cookie handling, dependency injection, and the
`require_owner` auth guard wired to `sessions.validate()`.

Contracts locked in here:

  POST /auth/login   correct passphrase → 200 + HttpOnly ora_session cookie
                     wrong passphrase   → 401
  POST /auth/logout  revokes the session; subsequent /auth/session → 401
  GET  /auth/session 200 with valid cookie, 401 without
  [owner] routes     return 401 with no valid session cookie (13 routes)
  public routes      return 200 with no auth (7 routes)
  POST /policies     owner creates a policy via HTTP; visible in GET /policies
  PUT  /policy/mode  owner sets enforcement mode; reflected in GET /policy/mode
  POST /agents/{id}/revoke + restore   via HTTP, requires owner auth
  POST /approvals/{id}/resolve         approves a real held action end-to-end
                                       (also covers the denial path)

Isolation strategy:

  db.DB_PATH and db._local are monkeypatched so every module's _connect()
  call (sessions, consent, policy, limits, ledger) uses a fresh tmp_path DB.
  crypto._DB_PATH is also redirected (nonce store).
  auth._passphrase is set to a known test value so no file I/O happens.
  ledger.ANCHOR_FILE and ledger._verify_checkpoint are reset for each test.
  The app module (main) is imported once (lazily) and reused — see _get_app().
"""
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

import auth
import consent
import db as _db
import ledger
import limits
import policy
import sessions

# Known passphrase injected into auth._passphrase by the fixture.
_PASSPHRASE = "route-test-owner-passphrase-xyz"

# Module-level singleton: main has module-level side-effects so we only import
# it once per pytest session, after the DB is already redirected (see _get_app).
_app = None


def _get_app():
    """Import the FastAPI app lazily, after DB and auth patches are in place."""
    global _app
    if _app is None:
        from main import app as _main_app
        _app = _main_app
    return _app


@pytest.fixture()
def route_client(tmp_path, monkeypatch, fresh_key):
    """TestClient wired to an isolated DB + known owner passphrase.

    Patches applied (and auto-restored after each test by monkeypatch):
      db.DB_PATH / db._local  → every module's _connect() hits the test DB
      crypto._DB_PATH         → nonce store goes to the same test DB
      auth._passphrase        → known test value; no file I/O
      ledger.ANCHOR_FILE      → anchor writes land in tmp_path
      ledger._verify_checkpoint → reset to genesis so incremental state is fresh
      policy._policy_cache    → cleared so first call loads from the test DB
      limits.ACTOR_HOURLY_LIMIT / DAILY_CAP → 0 to disable blast-radius caps
    """
    import crypto as _crypto

    db_file    = tmp_path / "oracle.db"
    anchor_log = tmp_path / "anchor.log"

    # Redirect all DB operations.  Replace _local entirely so no cached
    # connection from a previous test leaks in.
    monkeypatch.setattr(_db, "DB_PATH",  str(db_file))
    monkeypatch.setattr(_db, "_local",   threading.local())

    # Redirect crypto's nonce DB (sqlite3.connect called directly there).
    monkeypatch.setattr(_crypto, "_DB_PATH", str(db_file))

    # Redirect the anchor log and reset the incremental checkpoint.
    monkeypatch.setattr(ledger, "ANCHOR_FILE",        str(anchor_log))
    monkeypatch.setattr(ledger, "_verify_checkpoint", {"id": 0, "hash": ledger.GENESIS_HASH})

    # Fresh policy state and no rate-limiting.
    monkeypatch.setattr(policy, "_policy_cache",      None)
    monkeypatch.setattr(limits, "ACTOR_HOURLY_LIMIT", 0)
    monkeypatch.setattr(limits, "DAILY_CAP",          0)

    # Known owner passphrase — avoids reading from oracle_keys/owner.token.
    monkeypatch.setattr(auth, "_passphrase", _PASSPHRASE)

    # Reset executor so no stale callable bleeds in from a previous test.
    consent._executor = None

    # Create all tables in the isolated DB.
    consent.init_db()
    sessions.init_table()

    # Import (or reuse cached) FastAPI app — lazily, so the first import
    # happens with the test DB already in place (main.py calls consent.init_db()
    # at module level, so its tables land in the test DB on first import).
    app = _get_app()

    # Not used as context manager → startup event (monitor.verify_loop) does
    # not run, which is what we want: no background task, no interference.
    client = TestClient(app, raise_server_exceptions=True)
    yield client

    # Teardown: clear the executor so the next fixture starts clean.
    consent._executor = None


def _login(client: TestClient) -> None:
    """Log in with the test passphrase; TestClient stores the session cookie."""
    r = client.post("/auth/login", json={"passphrase": _PASSPHRASE})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"


# ===========================================================================
# Auth routes
# ===========================================================================

def test_login_correct_passphrase(route_client):
    r = route_client.post("/auth/login", json={"passphrase": _PASSPHRASE})
    assert r.status_code == 200
    assert r.json().get("ok") is True
    assert "ora_session" in r.cookies


def test_login_wrong_passphrase(route_client):
    r = route_client.post("/auth/login", json={"passphrase": "not-the-right-one"})
    assert r.status_code == 401


def test_logout_revokes_session_cookie(route_client):
    """After logout the session cookie no longer grants access."""
    _login(route_client)

    r = route_client.post("/auth/logout")
    assert r.status_code == 200
    assert r.json().get("ok") is True

    # The cookie must have been cleared in the TestClient's jar.
    r2 = route_client.get("/auth/session")
    assert r2.status_code == 401


def test_session_status_with_valid_cookie(route_client):
    _login(route_client)
    r = route_client.get("/auth/session")
    assert r.status_code == 200
    assert r.json().get("authenticated") is True


def test_session_status_without_cookie(route_client):
    r = route_client.get("/auth/session")
    assert r.status_code == 401


# ===========================================================================
# Owner-only routes return 401 without a valid session
# ===========================================================================

@pytest.mark.parametrize("method,path,body", [
    # Observability (read-only but owner-gated)
    ("GET",    "/auth/session",             None),
    ("GET",    "/monitor/status",           None),
    ("GET",    "/limits/status",            None),
    ("GET",    "/provenance/patterns",      None),
    # Key management
    ("GET",    "/keys/status",              None),
    ("GET",    "/keys/backup",              None),
    # Policy writes
    ("POST",   "/policies",                 {"rule_type": "action_deny", "params": {"action": "x"}}),
    ("DELETE", "/policies/1",               None),
    ("PUT",    "/policy/mode",              {"mode": "enforced"}),
    # Agent management
    ("POST",   "/agents/ora.core/revoke",   None),
    ("POST",   "/agents/ora.core/restore",  None),
    ("POST",   "/agents/register",          {"name": "new-agent"}),
    # Approval resolution
    ("POST",   "/approvals/fake/resolve",   {"decision": "approve"}),
])
def test_owner_route_requires_auth(route_client, method, path, body):
    r = route_client.request(method, path, json=body)
    assert r.status_code == 401, (
        f"{method} {path} must return 401 without auth, got {r.status_code}: {r.text[:200]}"
    )


# ===========================================================================
# Public routes are accessible without authentication
# ===========================================================================

@pytest.mark.parametrize("path", [
    "/health",
    "/ledger",
    "/ledger/verify",
    "/ledger/summary",
    "/ledger/export",
    "/policies",
    "/policy/mode",
    "/agents",
    "/devices",
])
def test_public_route_no_auth_required(route_client, path):
    r = route_client.get(path)
    assert r.status_code == 200, (
        f"GET {path} must be public (200), got {r.status_code}: {r.text[:200]}"
    )


# ===========================================================================
# Owner writes: policy management
# ===========================================================================

def test_create_policy_via_http(route_client):
    """Owner creates an action_deny policy via HTTP; it appears in GET /policies."""
    _login(route_client)

    r = route_client.post(
        "/policies",
        json={
            "rule_type": "action_deny",
            "params":    {"action": "make_purchase"},
            "label":     "http-test deny",
        },
    )
    assert r.status_code == 200, f"POST /policies failed: {r.text}"
    created = r.json().get("policy", {})
    assert created.get("rule_type") == "action_deny"
    assert "id" in created

    # Created policy is visible in the public listing.
    r2 = route_client.get("/policies")
    assert r2.status_code == 200
    ids = [p["id"] for p in r2.json()["policies"]]
    assert created["id"] in ids


def test_create_policy_requires_rule_type_and_params(route_client):
    """Sending neither rule_type+params nor text returns a 400 error body."""
    _login(route_client)
    r = route_client.post("/policies", json={"label": "incomplete"})
    # The handler returns a JSONResponse({"error": ...}, status_code=400).
    assert r.status_code == 400


def test_set_policy_mode_via_http(route_client):
    _login(route_client)

    r = route_client.put("/policy/mode", json={"mode": "audit_only"})
    assert r.status_code == 200
    assert r.json().get("mode") == "audit_only"

    # Public GET reflects the change.
    r2 = route_client.get("/policy/mode")
    assert r2.json()["mode"] == "audit_only"


def test_set_invalid_policy_mode_returns_400(route_client):
    _login(route_client)
    r = route_client.put("/policy/mode", json={"mode": "nonexistent_mode"})
    assert r.status_code == 400


def test_delete_policy_via_http(route_client):
    """Owner can delete a policy by ID."""
    _login(route_client)

    # Create a policy to delete.
    r = route_client.post(
        "/policies",
        json={"rule_type": "action_deny", "params": {"action": "make_purchase"}},
    )
    policy_id = r.json()["policy"]["id"]

    r2 = route_client.delete(f"/policies/{policy_id}")
    assert r2.status_code == 200
    assert r2.json().get("deleted") is True

    # No longer in the listing.
    r3 = route_client.get("/policies")
    ids = [p["id"] for p in r3.json()["policies"]]
    assert policy_id not in ids


# ===========================================================================
# Owner writes: agent management
# ===========================================================================

def test_revoke_and_restore_agent_via_http(route_client):
    """Owner can revoke then restore a default agent via HTTP."""
    _login(route_client)

    # ora.shopping is seeded by consent.init_db() → always exists in the test DB.
    r = route_client.post("/agents/ora.shopping/revoke")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert body.get("status") == "revoked"

    r2 = route_client.post("/agents/ora.shopping/restore")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2.get("ok") is True
    assert body2.get("status") == "active"


def test_get_agents_is_public(route_client):
    """GET /agents lists all registered actors and requires no auth."""
    r = route_client.get("/agents")
    assert r.status_code == 200
    agents = r.json()["agents"]
    ids = [a["id"] for a in agents]
    # Default actors must be present after consent.init_db().
    assert "ora.core" in ids
    assert "ora.shopping" in ids


# ===========================================================================
# Approval queue: resolve held actions end-to-end via HTTP
# ===========================================================================

def _force_hold(executor_log: list) -> str:
    """Register an executor and create a held action; return the approval_id.

    Uses a zero-cap spend_limit policy to guarantee HOLD regardless of time.
    Clears existing seed-default policies first so no conflicting rules exist.
    """
    # Register an executor so consent.resolve() can run the action.
    consent.set_executor(lambda action, args: executor_log.append((action, args)) or "done")

    # Clear seed policies and add a zero-cap spend_limit → always HOLD.
    with sqlite3.connect(_db.DB_PATH) as conn:
        conn.execute("DELETE FROM policies")
        conn.commit()
    if hasattr(_db._local, "conn"):
        delattr(_db._local, "conn")
    policy.add_policy("spend_limit", {"max_amount": 0}, label="zero-cap")

    gate = consent.request_action(
        actor_id="ora.core",
        action="make_purchase",
        args={"item": "test-book", "amount": 1.0},
        execute=lambda: pytest.fail("action must be held, not executed"),
    )
    assert gate["status"] == "held", f"Expected held, got: {gate}"
    return gate["approval_id"]


def test_approve_held_action_via_http(route_client):
    """Owner approves a held action via POST /approvals/{id}/resolve."""
    executed: list = []
    approval_id = _force_hold(executed)

    _login(route_client)
    r = route_client.post(
        f"/approvals/{approval_id}/resolve",
        json={"decision": "approve"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True, f"Expected ok=True, got: {body}"
    assert body.get("status") == "executed"
    assert executed, "Executor must have been called on approval"


def test_deny_held_action_via_http(route_client):
    """Owner denies a held action; executor is never called."""
    executed: list = []
    approval_id = _force_hold(executed)

    _login(route_client)
    r = route_client.post(
        f"/approvals/{approval_id}/resolve",
        json={"decision": "deny"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True, f"Expected ok=True, got: {body}"
    assert body.get("status") == "denied"
    assert not executed, "Executor must NOT be called when the owner denies"


def test_resolve_nonexistent_approval_returns_error(route_client):
    """Resolving an unknown approval_id returns ok=False without a 5xx error."""
    _login(route_client)
    r = route_client.post(
        "/approvals/apr_does_not_exist/resolve",
        json={"decision": "approve"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False
    assert "error" in body


def test_get_approvals_returns_pending_holds(route_client):
    """GET /approvals (owner-only) lists held actions."""
    executed: list = []
    _force_hold(executed)

    _login(route_client)
    r = route_client.get("/approvals")
    assert r.status_code == 200
    approvals = r.json()["approvals"]
    assert any(a["action"] == "make_purchase" for a in approvals)
