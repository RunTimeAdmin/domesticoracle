"""
Ora's audit ledger: an append-only, hash-chained, SIGNED record of every consequential
action taken on the user's behalf.

Three layers of defence, each catching what the one before it can't:

1. Hash chain. Each entry's hash covers its contents plus the previous entry's hash. Edit
   or delete any past entry and every hash after it stops matching.

2. Ed25519 signatures. Each entry's hash is signed with the server key, which lives OUTSIDE
   the database. A motivated attacker with write access to oracle.db can recompute the hash
   chain to look internally consistent - but they cannot forge the signatures, so the forgery
   is still detectable. The public key is exportable, so anyone can verify independently.

3. External anchor. After each append, the new chain head (id + hash) is written to an
   append-only anchor file kept outside the database. This catches the one thing signatures
   alone don't: wholesale rollback or truncation. If someone deletes the DB and rebuilds a
   shorter or older chain, it may verify internally, but it won't match the last anchored
   head. (In production the anchor belongs on separate WORM/remote storage or a timestamping
   authority; here it's a local file as a working stand-in.)

Storage is a single SQLite table; volume is human-scale (actions per day).
"""
import os, json, sqlite3, hashlib, time, threading

import crypto
from db import connect as _connect

DB_PATH = os.path.join(os.path.dirname(__file__), "oracle.db")
ANCHOR_FILE = os.path.join(os.path.dirname(__file__), "oracle_keys", "anchor.log")
GENESIS_HASH = "0" * 64

_lock = threading.Lock()
_verify_lock = threading.Lock()
_verify_checkpoint: dict = {"id": 0, "hash": GENESIS_HASH}


def _migrate_ledger(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ledger)").fetchall()}
    if "args_json" not in cols:
        conn.execute("ALTER TABLE ledger ADD COLUMN args_json TEXT NOT NULL DEFAULT '{}'")
        conn.commit()


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           REAL    NOT NULL,
                actor_id     TEXT    NOT NULL,
                action       TEXT    NOT NULL,
                args_summary TEXT    NOT NULL,
                args_json    TEXT    NOT NULL DEFAULT '{}',
                decision     TEXT    NOT NULL,
                status       TEXT    NOT NULL,
                outcome      TEXT    NOT NULL DEFAULT '',
                prev_hash    TEXT    NOT NULL,
                hash         TEXT    NOT NULL,
                sig          TEXT    NOT NULL DEFAULT '',
                category     TEXT    NOT NULL DEFAULT '',
                risk         INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        _migrate_ledger(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ledger_action_status_ts "
            "ON ledger(action, status, ts)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ledger_ts ON ledger(ts)")
        conn.commit()


def _canonical(entry: dict) -> str:
    """Stable serialization for hashing. Covers args_json, category, and risk so those
    fields carry integrity protection from the moment they are written."""
    payload = {
        "action": entry["action"],
        "actor_id": entry["actor_id"],
        "args_json": entry.get("args_json", "{}"),
        "args_summary": entry["args_summary"],
        "category": entry.get("category", ""),
        "decision": entry["decision"],
        "outcome": entry["outcome"],
        "prev_hash": entry["prev_hash"],
        "risk": int(entry.get("risk", 0)),
        "status": entry["status"],
        "ts": entry["ts"],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _canonical_legacy(entry: dict) -> str:
    """Hash payload used before the args_json/category/risk expansion. Accepted by
    verify_chain() for rows written before this patch so existing ledgers stay valid."""
    payload = {
        "ts": entry["ts"],
        "actor_id": entry["actor_id"],
        "action": entry["action"],
        "args_summary": entry["args_summary"],
        "decision": entry["decision"],
        "status": entry["status"],
        "outcome": entry["outcome"],
        "prev_hash": entry["prev_hash"],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _compute_hash(entry: dict) -> str:
    return hashlib.sha256(_canonical(entry).encode("utf-8")).hexdigest()


def _write_anchor(entry_id: int, entry_hash: str) -> None:
    """Append the new chain head to the external anchor file (best-effort, append-only)."""
    os.makedirs(os.path.dirname(ANCHOR_FILE), exist_ok=True)
    line = {
        "id": entry_id,
        "hash": entry_hash,
        "ts": time.time(),
        "sig": crypto.sign(f"{entry_id}:{entry_hash}".encode("utf-8")),
    }
    with open(ANCHOR_FILE, "a") as f:
        f.write(json.dumps(line, separators=(",", ":")) + "\n")


def append(actor_id: str, action: str, args_summary: str,
           decision: str, status: str, outcome: str = "",
           category: str = "", risk: int = 0, args: dict = None) -> dict:
    """Append a new entry: link it to the chain head, sign it, and anchor it.

    `args` is stored as canonical JSON and included in the signed payload.
    `args_summary` is kept for display. `category` and `risk` are now also signed.
    """
    args_json_str = json.dumps(args or {}, sort_keys=True, separators=(",", ":"))
    with _lock, _connect() as conn:
        row = conn.execute("SELECT hash FROM ledger ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = row["hash"] if row else GENESIS_HASH

        entry = {
            "ts": time.time(),
            "actor_id": actor_id,
            "action": action,
            "args_summary": args_summary,
            "args_json": args_json_str,
            "decision": decision,
            "status": status,
            "outcome": outcome,
            "prev_hash": prev_hash,
            "category": category,
            "risk": int(risk),
        }
        entry["hash"] = _compute_hash(entry)
        entry["sig"] = crypto.sign(entry["hash"].encode("utf-8"))

        cur = conn.execute(
            """INSERT INTO ledger
               (ts, actor_id, action, args_summary, args_json, decision, status, outcome,
                prev_hash, hash, sig, category, risk)
               VALUES (:ts, :actor_id, :action, :args_summary, :args_json, :decision, :status,
                       :outcome, :prev_hash, :hash, :sig, :category, :risk)""",
            entry,
        )
        conn.commit()
        entry["id"] = cur.lastrowid

    _write_anchor(entry["id"], entry["hash"])
    return entry


def update_outcome(entry_id: int, status: str, outcome: str, decision: str = "n/a") -> None:
    """Append a NEW entry recording the resolution of a prior held action.

    We never mutate existing rows - that would break the chain by design. Instead a
    follow-up entry references the original in its summary, preserving the audit trail.
    """
    append(
        actor_id="ora.system",
        action="resolve",
        args_summary=f"resolves ledger entry #{entry_id}",
        decision=decision,
        status=status,
        outcome=outcome,
        category="governance",
        risk=0,
    )


def list_entries(limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM ledger ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def sum_today(action: str, fields=("amount", "cost", "price", "total")) -> float:
    """Total of a numeric arg across today's EXECUTED entries for an action.

    Used by the policy engine for per-day spend caps.
    Fast path: SQL JSON1 aggregation (single in-engine SUM, no per-row Python).
    Fallback: string parsing for legacy rows without structured args.
    """
    start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    conn = _connect()
    primary = fields[0]
    row = conn.execute(
        "SELECT COALESCE(SUM(CAST(json_extract(args_json,?) AS REAL)), 0) "
        "FROM ledger WHERE action=? AND status='executed' AND ts>=? "
        "AND json_extract(args_json,?) IS NOT NULL",
        (f"$.{primary}", action, start, f"$.{primary}"),
    ).fetchone()
    total = float(row[0] or 0.0)
    rows = conn.execute(
        "SELECT args_summary FROM ledger "
        "WHERE action=? AND status='executed' AND ts>=? "
        "AND (args_json IS NULL OR args_json='{}')",
        (action, start),
    ).fetchall()
    for r in rows:
        for part in str(r["args_summary"]).split(","):
            if "=" not in part:
                continue
            k, _, v = part.partition("=")
            if k.strip() in fields:
                try:
                    total += float(v.strip())
                except ValueError:
                    pass
                break
    return total


def summary(days: int = 7) -> dict:
    """Rolling-window rollup for the Trust Center: what happened, by category and risk.

    A home-sized version of CounterAudit's agentic-debt index - legible at a glance.
    """
    start = time.time() - days * 86400
    with _connect() as conn:
        rows = conn.execute(
            "SELECT action, category, risk, decision, status, args_summary, args_json "
            "FROM ledger WHERE ts >= ?", (start,)
        ).fetchall()

    by_category: dict[str, int] = {}
    held = denied = executed = 0
    risks: list[int] = []
    financial_total = 0.0

    for r in rows:
        cat = r["category"] or "other"
        by_category[cat] = by_category.get(cat, 0) + 1
        risks.append(int(r["risk"] or 0))
        status = r["status"]
        if status in ("pending",):
            held += 1
        elif status in ("blocked", "denied"):
            denied += 1
        elif status == "executed":
            executed += 1
        if r["action"] == "make_purchase" and status == "executed":
            parsed = None
            try:
                parsed = json.loads(r["args_json"] or "{}") or None
            except (ValueError, TypeError):
                pass
            found = False
            if parsed:
                for field in ("amount", "cost", "price", "total"):
                    if field in parsed:
                        try:
                            financial_total += float(parsed[field])
                        except (ValueError, TypeError):
                            pass
                        found = True
                        break
            if not found:
                for part in str(r["args_summary"]).split(","):
                    k, _, v = part.partition("=")
                    if k.strip() in ("amount", "cost", "price", "total"):
                        try:
                            financial_total += float(v.strip())
                        except ValueError:
                            pass
                        break

    return {
        "window_days": days,
        "total": len(rows),
        "by_category": by_category,
        "held": held,
        "denied": denied,
        "executed": executed,
        "financial_total": round(financial_total, 2),
        "avg_risk": round(sum(risks) / len(risks), 1) if risks else 0,
        "max_risk": max(risks) if risks else 0,
        "trust_load": sum(risks),  # cumulative risk handled this week
    }


def verify_chain(full: bool = False) -> dict:
    """Verify hash links and Ed25519 signatures.

    By default resumes from the last verified checkpoint, so repeated calls
    are O(new entries) rather than O(N). Pass full=True to re-scan from
    genesis (e.g. after suspecting tampering of old entries).

    Accepts both the current canonical format and the legacy format (rows
    written before the args_json/category/risk expansion).
    """
    with _verify_lock:
        conn = _connect()
        start_id = 0 if full else _verify_checkpoint["id"]
        prev_hash = GENESIS_HASH if full else _verify_checkpoint["hash"]

        rows = conn.execute(
            "SELECT * FROM ledger WHERE id > ? ORDER BY id ASC", (start_id,)
        ).fetchall()

        checked = start_id
        for r in rows:
            entry = dict(r)
            if entry["prev_hash"] != prev_hash:
                return {"valid": False, "checked": checked,
                        "broken_at": entry["id"], "reason": "Hash chain link broken."}
            if _compute_hash(entry) != entry["hash"]:
                legacy = hashlib.sha256(_canonical_legacy(entry).encode("utf-8")).hexdigest()
                if legacy != entry["hash"]:
                    return {"valid": False, "checked": checked,
                            "broken_at": entry["id"],
                            "reason": "Entry contents do not match its hash."}
            if not entry.get("sig") or not crypto.verify(
                entry["hash"].encode("utf-8"), entry["sig"]
            ):
                return {"valid": False, "checked": checked,
                        "broken_at": entry["id"],
                        "reason": "Entry signature is missing or invalid (forged)."}
            prev_hash = entry["hash"]
            checked = entry["id"]

        _verify_checkpoint["id"] = checked
        _verify_checkpoint["hash"] = prev_hash

    total = conn.execute("SELECT COUNT(*) c FROM ledger").fetchone()["c"]
    return {"valid": True, "checked": total, "broken_at": None, "reason": "Chain intact."}


def verify_anchor() -> dict:
    """Check the live chain against the last external anchor to catch rollback/truncation.

    Returns {"anchored", "consistent", "reason"}.
    """
    if not os.path.exists(ANCHOR_FILE):
        return {"anchored": False, "consistent": True, "reason": "No anchor yet."}
    last = None
    with open(ANCHOR_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                last = line
    if not last:
        return {"anchored": False, "consistent": True, "reason": "Anchor file empty."}

    rec = json.loads(last)
    if not crypto.verify(f"{rec['id']}:{rec['hash']}".encode("utf-8"), rec["sig"]):
        return {"anchored": True, "consistent": False, "reason": "Anchor signature invalid."}

    with _connect() as conn:
        row = conn.execute("SELECT hash FROM ledger WHERE id = ?", (rec["id"],)).fetchone()
    if not row:
        return {"anchored": True, "consistent": False,
                "reason": f"Anchored entry #{rec['id']} is missing (rollback/truncation)."}
    if row["hash"] != rec["hash"]:
        return {"anchored": True, "consistent": False,
                "reason": f"Anchored entry #{rec['id']} hash differs (history rewritten)."}
    return {"anchored": True, "consistent": True, "reason": "Matches last anchor."}


def integrity(full: bool = False) -> dict:
    """Combined verdict used by the API and Trust Center badge."""
    chain = verify_chain(full=full)
    anchor = verify_anchor()
    valid = chain["valid"] and anchor["consistent"]
    return {
        "valid": valid,
        "checked": chain["checked"],
        "broken_at": chain["broken_at"],
        "reason": chain["reason"] if not chain["valid"] else anchor["reason"],
        "chain": chain,
        "anchor": anchor,
        "public_key": crypto.server_public_key_hex(),
    }
