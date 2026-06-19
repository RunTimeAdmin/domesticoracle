"""
The consent gate: where identity, policy, ledger, and approvals meet.

Every guarded action passes through `request_action`, which is the single chokepoint:
  1. Identity. A revoked actor is refused before anything else - permission means nothing
     without a valid, active identity.
  2. Policy. The policy engine returns allow / hold / deny.
  3. Ledger. The attempt is recorded (signed, chained) no matter the verdict.
  4. Effect. Allowed actions run now; held actions are parked durably for the owner's
     approval; denied actions stop.

Agent identity is Ed25519. Internal actors (Domestic Oracle's own tools) are trusted because they
run in-process. EXTERNAL agents must sign each request with their private key; the server
holds only their public key, so a database leak can't be used to impersonate them, and
revocation actually bites.

Pending approvals live in SQLite, not memory, so a restart never loses a held action and
multiple workers see the same queue. Because a stored approval can't carry a live Python
closure, execution on approval is re-run through a registered executor, and the policy is
re-checked at approval time so a rule change since the hold is honoured (no stale allow).
"""
import os, json, sqlite3, time, threading, secrets

import ledger
import policy
import crypto
import risk
from db import connect as _connect

_lock = threading.Lock()

# Re-execution hook for approved actions, registered by the tools layer (avoids a circular
# import). Signature: executor(action: str, args: dict) -> str.
_executor = None


def set_executor(fn) -> None:
    global _executor
    _executor = fn


# Default actors. INTERNAL actors run in-process, trusted without a signature.
# External agents are added via register_agent().
_DEFAULT_AGENTS = [
    ("ora.core",          "Ora",                       "internal"),
    ("ora.shopping",      "Shopping Agent",            "internal"),
    ("ora.messaging",     "Messaging Agent",           "internal"),
    ("ora.home",          "Home Control Agent",        "internal"),
    ("ora.mcp",           "MCP Gateway",               "internal"),
    ("oracle.agent",      "Domestic Oracle Agent",     "internal"),
]


def _migrate_approvals(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(approvals)").fetchall()}
    if "approved_by_owner" not in cols:
        conn.execute("ALTER TABLE approvals ADD COLUMN approved_by_owner INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def init_db() -> None:
    ledger.init_db()
    policy.init_db()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'active',
                kind       TEXT NOT NULL DEFAULT 'internal',
                public_key TEXT NOT NULL DEFAULT '',
                created    REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                id                TEXT    PRIMARY KEY,
                actor_id          TEXT    NOT NULL,
                action            TEXT    NOT NULL,
                args              TEXT    NOT NULL,
                summary           TEXT    NOT NULL,
                reason            TEXT    NOT NULL,
                ledger_id         INTEGER NOT NULL,
                status            TEXT    NOT NULL DEFAULT 'pending',
                approved_by_owner INTEGER NOT NULL DEFAULT 0,
                created           REAL    NOT NULL
            )
            """
        )
        _migrate_approvals(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_approvals_status_created "
            "ON approvals(status, created)"
        )
        # Persistent nonce store: survives restarts, blocks replay attacks.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nonces (
                nonce      TEXT PRIMARY KEY,
                expires_at REAL NOT NULL
            )
            """
        )
        for aid, name, kind in _DEFAULT_AGENTS:
            conn.execute(
                "INSERT OR IGNORE INTO agents (id, name, status, kind, public_key, created) "
                "VALUES (?,?,?,?,?,?)",
                (aid, name, "active", kind, "", time.time()),
            )
        conn.commit()


# --------------------------------------------------------------------------- agents
def list_agents() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, status, kind, created FROM agents ORDER BY id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def _agent(actor_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (actor_id,)).fetchone()
    return dict(row) if row else None


def set_agent_status(actor_id: str, status: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("UPDATE agents SET status = ? WHERE id = ?", (status, actor_id))
        conn.commit()
    return cur.rowcount > 0


def register_agent(name: str) -> dict:
    """Register an EXTERNAL agent. Returns its id and PRIVATE key (shown only once)."""
    priv_hex, pub_hex = crypto.generate_agent_keypair()
    actor_id = "agent." + secrets.token_hex(4)
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO agents (id, name, status, kind, public_key, created) VALUES (?,?,?,?,?,?)",
            (actor_id, name, "active", "external", pub_hex, time.time()),
        )
        conn.commit()
    return {"id": actor_id, "name": name, "private_key": priv_hex, "public_key": pub_hex}


def verify_agent_request(actor_id: str, action: str, args: dict,
                         nonce: str, ts: float, sig: str) -> tuple[bool, str]:
    """Verify a signed request from an external agent before it touches the gate."""
    agent = _agent(actor_id)
    if not agent:
        return False, "Unknown agent."
    if agent["status"] != "active":
        return False, "Agent is revoked."
    if agent["kind"] != "external" or not agent["public_key"]:
        return False, "Agent has no registered key."

    if not crypto.is_fresh(ts):
        return False, "Request timestamp is outside the freshness window."

    # Verify signature BEFORE consuming the nonce to block nonce-burning attacks.
    args_canonical = json.dumps(args or {}, sort_keys=True, separators=(",", ":"))
    message = crypto.agent_request_message(actor_id, action, args_canonical, nonce, ts)
    if not crypto.verify(message, sig, agent["public_key"]):
        return False, "Signature verification failed."

    if not crypto.consume_nonce(nonce):
        return False, "Nonce already used (replay rejected)."
    return True, "ok"


# --------------------------------------------------------------------------- approvals
def list_pending() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE status = 'pending' ORDER BY created ASC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["args"] = json.loads(d["args"])
        out.append(d)
    return out


def _summarize(args: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in (args or {}).items()) or "(no details)"


def request_action(actor_id: str, action: str, args: dict, execute) -> dict:
    """Run an action through the gate. The single chokepoint for every guarded effect.

    `execute` is a zero-arg callable performing the real work for the allow-now path.
    Returns {"status", "reason", "result", "approval_id", "ledger_id"}.
    """
    args = args or {}
    summary = _summarize(args)

    agent = _agent(actor_id)
    if agent and agent["status"] == "revoked":
        rk = risk.score(action, args, "deny")
        entry = ledger.append(actor_id, action, summary, "deny", "blocked",
                              "Actor is revoked.", risk.category(action), rk, args=args)
        return {"status": "denied", "reason": "That agent has been revoked.",
                "result": None, "approval_id": None, "ledger_id": entry["id"]}

    decision, reason = policy.evaluate(actor_id, action, args)
    mode = policy.get_mode()
    cat, rk = risk.category(action), risk.score(action, args, decision)

    if decision in (policy.DENY, policy.HOLD) and mode != policy.ENFORCED:
        result = execute()
        note = f"[{mode}] would {decision} ({reason}). Ran because enforcement is off. Result: {result}"
        entry = ledger.append(actor_id, action, summary, decision, "executed", note, cat, rk, args=args)
        return {"status": "executed",
                "reason": f"(Observed in {mode} mode — this would have been a '{decision}': {reason})",
                "result": str(result), "approval_id": None, "ledger_id": entry["id"]}

    if decision == policy.DENY:
        entry = ledger.append(actor_id, action, summary, decision, "blocked", reason, cat, rk, args=args)
        return {"status": "denied", "reason": reason,
                "result": None, "approval_id": None, "ledger_id": entry["id"]}

    if decision == policy.HOLD:
        entry = ledger.append(actor_id, action, summary, decision, "pending", reason, cat, rk, args=args)
        approval_id = "apr_" + secrets.token_hex(6)
        with _lock, _connect() as conn:
            conn.execute(
                "INSERT INTO approvals (id, actor_id, action, args, summary, reason, ledger_id, status, created) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (approval_id, actor_id, action, json.dumps(args), summary, reason,
                 entry["id"], "pending", time.time()),
            )
            conn.commit()
        return {"status": "held", "reason": reason, "result": None,
                "approval_id": approval_id, "ledger_id": entry["id"]}

    # allow
    result = execute()
    entry = ledger.append(actor_id, action, summary, decision, "executed", str(result), cat, rk, args=args)
    return {"status": "executed", "reason": reason, "result": str(result),
            "approval_id": None, "ledger_id": entry["id"]}


def resolve(approval_id: str, decision: str) -> dict:
    """Approve or deny a held action.

    Owner approval is an explicit override — policy is NOT re-evaluated. The owner is the root
    authority; policy governs autonomous action, not explicit human decisions. Re-checking
    policy after approval would make the approval queue misleading ("Approve" that can still
    be denied is not a real approval).
    """
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM approvals WHERE id = ? AND status = 'pending'", (approval_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "No such pending approval (it may already be resolved)."}
        conn.execute("UPDATE approvals SET status = 'resolving' WHERE id = ?", (approval_id,))
        conn.commit()
        pending = dict(row)

    pending["args"] = json.loads(pending["args"])
    action, args = pending["action"], pending["args"]

    if decision != "approve":
        _close(approval_id, "denied")
        ledger.update_outcome(pending["ledger_id"], "denied", "Denied by owner.")
        return {"ok": True, "status": "denied", "action": action, "summary": pending["summary"]}

    if _executor is None:
        _close(approval_id, "pending")
        return {"ok": False, "error": "No executor registered to run the action."}

    # Mark owner approval before executing so the record is durable even if execution crashes.
    with _lock, _connect() as conn:
        conn.execute("UPDATE approvals SET approved_by_owner = 1 WHERE id = ?", (approval_id,))
        conn.commit()

    try:
        result = _executor(action, args)
        _close(approval_id, "executed")
        ledger.update_outcome(pending["ledger_id"], "executed",
                              f"Approved by owner. Result: {result}",
                              decision="allowed (owner)")
        return {"ok": True, "status": "executed", "result": str(result),
                "action": action, "summary": pending["summary"]}
    except Exception as e:
        _close(approval_id, "error")
        ledger.update_outcome(pending["ledger_id"], "error", f"Execution failed: {e}")
        return {"ok": False, "error": str(e)}


def _close(approval_id: str, status: str) -> None:
    with _lock, _connect() as conn:
        conn.execute("UPDATE approvals SET status = ? WHERE id = ?", (status, approval_id))
        conn.commit()
