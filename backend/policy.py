"""
Ora's policy engine: the rules that decide whether an action runs, waits for you,
or is blocked outright.

A policy is a small structured rule. `evaluate()` runs an action's details against
all active rules and returns the strictest verdict: deny beats hold beats allow.
Rules are stored in SQLite so they survive restarts.

Natural-language intake: `parse_policy("never spend more than $50")` asks Claude to
translate plain English into one of the structured rule types below, so the user can
set policy by talking instead of filling in forms. The parsed rule is always echoed
back for confirmation before it takes effect.

Rule types (Phase 1):
  spend_limit     {"max_amount": 50}                 hold purchases over the cap
  time_window     {"start_hour": 23, "end_hour": 6}  hold actions inside the window
  action_deny     {"action": "send_message"}         block an action type outright
  recipient_block {"recipient": "mom"}               block messages to a recipient
"""
import os, json, sqlite3, time, datetime, threading

import ledger
from db import connect as _connect

_lock = threading.Lock()
_cache_lock = threading.Lock()
_policy_cache: list[dict] | None = None

ALLOW, HOLD, DENY = "allow", "hold", "deny"
_SEVERITY = {ALLOW: 0, HOLD: 1, DENY: 2}


ENFORCED, AUDIT_ONLY, PERMISSIVE = "enforced", "audit_only", "permissive"
_MODES = {ENFORCED, AUDIT_ONLY, PERMISSIVE}


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS policies (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                created   REAL NOT NULL,
                rule_type TEXT NOT NULL,
                params    TEXT NOT NULL,
                source    TEXT NOT NULL DEFAULT 'manual',
                label     TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('policy_mode', ?)", (ENFORCED,)
        )
        conn.commit()
    _seed_defaults()


def get_mode() -> str:
    """Current enforcement posture.

    enforced    - holds and denies are enforced (default).
    audit_only  - nothing is blocked; the gate records what it WOULD have done, so the owner
                  can watch for a while before turning enforcement on. (Borrowed from Countersig.)
    permissive  - allow everything, log minimally.
    """
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'policy_mode'").fetchone()
    return row["value"] if row else ENFORCED


def set_mode(mode: str) -> str:
    if mode not in _MODES:
        raise ValueError(f"Unknown policy mode: {mode}")
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('policy_mode', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (mode,)
        )
        conn.commit()
    return mode


_DEFAULTS = [
    ("spend_limit",     {"max_amount": 50},
     "Hold any purchase over $50 for my approval"),
    ("time_window",     {"start_hour": 23, "end_hour": 6},
     "Hold device and purchase actions between 11pm and 6am"),
    ("recipient_block", {"recipient": "all_contacts"},
     "Never message my contacts as me without approval"),
]


def _seed_defaults() -> None:
    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM policies").fetchone()["c"]
        if count:
            return
        for rule_type, params, label in _DEFAULTS:
            conn.execute(
                "INSERT INTO policies (created, rule_type, params, source, label) VALUES (?,?,?,?,?)",
                (time.time(), rule_type, json.dumps(params), "default", label),
            )
        conn.commit()


def list_policies() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM policies ORDER BY id ASC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["params"] = json.loads(d["params"])
        out.append(d)
    return out


def _cached_policies() -> list[dict]:
    """Return policies from the in-process cache, populating it on first call.

    Policies change rarely but are evaluated on every guarded action. Caching
    eliminates per-action DB reads and JSON decoding. Returns copies so callers
    cannot mutate the cache. Invalidated by add_policy() and delete_policy().
    """
    global _policy_cache
    with _cache_lock:
        if _policy_cache is None:
            _policy_cache = list_policies()
        return [dict(p) for p in _policy_cache]


def _invalidate_cache() -> None:
    global _policy_cache
    with _cache_lock:
        _policy_cache = None


def add_policy(rule_type: str, params: dict, label: str = "", source: str = "manual") -> dict:
    if rule_type not in {"spend_limit", "time_window", "action_deny", "recipient_block"}:
        raise ValueError(f"Unknown rule_type: {rule_type}")
    with _lock, _connect() as conn:
        cur = conn.execute(
            "INSERT INTO policies (created, rule_type, params, source, label) VALUES (?,?,?,?,?)",
            (time.time(), rule_type, json.dumps(params), source, label),
        )
        conn.commit()
        pid = cur.lastrowid
    _invalidate_cache()
    return {"id": pid, "rule_type": rule_type, "params": params, "label": label, "source": source}


def delete_policy(policy_id: int) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM policies WHERE id = ?", (policy_id,))
        conn.commit()
    _invalidate_cache()
    return cur.rowcount > 0


def _amount(args: dict) -> float:
    for k in ("amount", "cost", "price", "total"):
        if k in args:
            try:
                return float(args[k])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _in_window(start_hour: int, end_hour: int, now_hour: int) -> bool:
    if start_hour <= end_hour:
        return start_hour <= now_hour < end_hour
    # Window wraps past midnight, e.g. 23 -> 6
    return now_hour >= start_hour or now_hour < end_hour


def evaluate(actor_id: str, action: str, args: dict) -> tuple[str, str]:
    """Return (decision, reason) for an action, applying the strictest matching rule."""
    args = args or {}
    decision, reason = ALLOW, "No policy restricts this action."
    # Local time — time_window rules are entered in natural language ("after 11pm")
    # and should match what the homeowner's wall clock reads.  The daily cap in
    # limits.py resets at UTC midnight instead; that split is intentional and
    # documented there.
    now_hour = datetime.datetime.now().hour

    for rule in _cached_policies():
        rtype, p = rule["rule_type"], rule["params"]
        verdict, why = None, None

        if rtype == "spend_limit" and action == "make_purchase":
            amt = _amount(args)
            cap = p.get("max_amount")
            if cap is not None and amt > float(cap):
                verdict = HOLD
                why = f"Purchase of ${amt:.2f} exceeds your ${float(cap):.2f} per-purchase limit."
            day_cap = p.get("max_per_day")
            if verdict is None and day_cap is not None:
                spent = ledger.sum_today("make_purchase")
                if spent + amt > float(day_cap):
                    verdict = HOLD
                    why = (f"This would bring today's spending to ${spent + amt:.2f}, "
                           f"over your ${float(day_cap):.2f} daily limit.")

        elif rtype == "time_window" and action in ("make_purchase", "control_device"):
            if _in_window(int(p.get("start_hour", 23)), int(p.get("end_hour", 6)), now_hour):
                verdict = HOLD
                why = f"It's outside your allowed hours for {action.replace('_', ' ')}."

        elif rtype == "action_deny" and action == p.get("action"):
            verdict = DENY
            why = f"Your policy blocks '{action}' outright."

        elif rtype == "recipient_block" and action == "send_message":
            target = str(args.get("recipient", "")).lower()
            blocked = str(p.get("recipient", "")).lower()
            if blocked == "all_contacts" or (blocked and blocked in target):
                verdict = HOLD
                why = f"Messaging '{args.get('recipient', 'someone')}' as you needs approval."

        if verdict and _SEVERITY[verdict] > _SEVERITY[decision]:
            decision, reason = verdict, why

    return decision, reason


# --------------------------------------------------------------------------- NL intake
_PARSE_PROMPT = """You translate a user's plain-English household rule into ONE structured policy.

Return ONLY a JSON object, no prose, with this shape:
{"rule_type": "<type>", "params": {...}, "label": "<short restated rule>"}

Valid rule_type values and their params:
- "spend_limit":     {"max_amount": <number>, "max_per_day": <number, optional>}   limits on spending; max_amount is per purchase, max_per_day is a daily total
- "time_window":     {"start_hour": <0-23>, "end_hour": <0-23>}  for "no actions after X / between X and Y" (use 24h hours)
- "action_deny":     {"action": "make_purchase"|"send_message"|"control_device"}  to block an action type entirely
- "recipient_block": {"recipient": "<name or 'all_contacts'>"}   to block messaging someone

Examples:
"never spend more than 100 dollars" -> {"rule_type":"spend_limit","params":{"max_amount":100},"label":"Hold purchases over $100"}
"don't let anything control devices after 10pm" -> {"rule_type":"time_window","params":{"start_hour":22,"end_hour":6},"label":"Hold device actions between 10pm and 6am"}
"never text my boss" -> {"rule_type":"recipient_block","params":{"recipient":"boss"},"label":"Hold messages to boss"}

User rule: {text}"""


def parse_policy(text: str, claude) -> dict:
    """Use Claude to convert plain English into a structured rule. Returns the rule dict.

    `claude` is an Anthropic client. Raises ValueError if the result isn't usable.
    """
    import os as _os
    model = _os.getenv("ORA_MODEL", _os.getenv("DORA_MODEL", "claude-haiku-4-5-20251001"))
    resp = claude.messages.create(
        model=model,
        max_tokens=300,
        messages=[{"role": "user", "content": _PARSE_PROMPT.replace("{text}", text)}],
    )
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    # Strip code fences if the model added them.
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):raw.rfind("}") + 1]
    try:
        rule = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError(f"Could not parse that into a rule. Got: {raw[:200]}")
    if "rule_type" not in rule or "params" not in rule:
        raise ValueError("Parsed rule missing required fields.")
    return rule
