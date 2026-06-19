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

DB_PATH = os.path.join(os.path.dirname(__file__), "oracle.db")
ANCHOR_FILE = os.path.join(os.path.dirname(__file__), "oracle_keys", "anchor.log")
GENESIS_HASH = "0" * 64

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL    NOT NULL,
                actor_id    TEXT    NOT NULL,
                action      TEXT    NOT NULL,
                args_summary TEXT   NOT NULL,
                decision    TEXT    NOT NULL,
                status      TEXT    NOT NULL,
                outcome     TEXT    NOT NULL DEFAULT '',
                prev_hash   TEXT    NOT NULL,
                hash        TEXT    NOT NULL,
                sig         TEXT    NOT NULL DEFAULT '',
                category    TEXT    NOT NULL DEFAULT '',
                risk        INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()


def _canonical(entry: dict) -> str:
    """Stable serialization of the hashable fields, independent of dict ordering."""
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
           category: str = "", risk: int = 0) -> dict:
    """Append a new entry: link it to the chain head, sign it, and anchor it.

    `category` and `risk` are derived metadata for the Trust Center summary. They are stored
    but intentionally NOT part of the hashed/signed payload - integrity covers the factual
    record (who did what, and the verdict), not the cosmetic score.
    """
    with _lock, _connect() as conn:
        row = conn.execute("SELECT hash FROM ledger ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = row["hash"] if row else GENESIS_HASH

        entry = {
            "ts": time.time(),
            "actor_id": actor_id,
            "action": action,
            "args_summary": args_summary,
            "decision": decision,
            "status": status,
            "outcome": outcome,
            "prev_hash": prev_hash,
        }
        entry["hash"] = _compute_hash(entry)
        entry["sig"] = crypto.sign(entry["hash"].encode("utf-8"))

        cur = conn.execute(
            """INSERT INTO ledger
               (ts, actor_id, action, args_summary, decision, status, outcome, prev_hash, hash, sig, category, risk)
               VALUES (:ts, :actor_id, :action, :args_summary, :decision, :status, :outcome, :prev_hash, :hash, :sig, :category, :risk)""",
            {**entry, "category": category, "risk": int(risk)},
        )
        conn.commit()
        entry["id"] = cur.lastrowid
        entry["category"], entry["risk"] = category, int(risk)

    _write_anchor(entry["id"], entry["hash"])
    return entry


def update_outcome(entry_id: int, status: str, outcome: str) -> None:
    """Append a NEW entry recording the resolution of a prior held action.

    We never mutate existing rows - that would break the chain by design. Instead a
    follow-up entry references the original in its summary, preserving the audit trail.
    """
    append(
        actor_id="ora.system",
        action="resolve",
        args_summary=f"resolves ledger entry #{entry_id}",
        decision="n/a",
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

    Used by the policy engine for per-day spend caps. Reads from the args_summary, which is
    a 'k=v, k=v' string; tolerant of formatting.
    """
    start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    total = 0.0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT args_summary FROM ledger WHERE action = ? AND status = 'executed' AND ts >= ?",
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
            "SELECT action, category, risk, decision, status, args_summary "
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


def verify_chain() -> dict:
    """Verify the hash links AND the signature of every entry.

    Returns {"valid", "checked", "broken_at", "reason"}.
    """
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM ledger ORDER BY id ASC").fetchall()

    prev_hash = GENESIS_HASH
    for r in rows:
        entry = dict(r)
        if entry["prev_hash"] != prev_hash:
            return {"valid": False, "checked": len(rows), "broken_at": entry["id"],
                    "reason": "Hash chain link broken."}
        if _compute_hash(entry) != entry["hash"]:
            return {"valid": False, "checked": len(rows), "broken_at": entry["id"],
                    "reason": "Entry contents do not match its hash."}
        if not entry.get("sig") or not crypto.verify(entry["hash"].encode("utf-8"), entry["sig"]):
            return {"valid": False, "checked": len(rows), "broken_at": entry["id"],
                    "reason": "Entry signature is missing or invalid (forged)."}
        prev_hash = entry["hash"]

    return {"valid": True, "checked": len(rows), "broken_at": None, "reason": "Chain intact."}


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


def integrity() -> dict:
    """Combined verdict used by the API and Trust Center badge."""
    chain = verify_chain()
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
