"""
Lightweight risk scoring for ledger entries.

Borrowed in spirit from CounterAudit's "agentic debt" index, but sized
for a home: a small, explainable score (0-100) and a category per action,
so the Trust Center can show "here's what happened this week and how risky
it leaned" without anyone needing a SOC.

Scoring is deliberately simple and legible - a homeowner should be able to
understand why something scored the way it did, not trust a black box.
"""

CATEGORY = {
    "make_purchase":   "financial",
    "send_message":    "communication",
    "send_email":      "communication",
    "reply_to_email":  "communication",
    "control_device":  "home_control",
    "search_web":      "read",
    "web_fetch":       "read",
    "get_weather":     "read",
    "discover_devices":"read",
    "read_inbox":      "read",
    "add_policy":      "governance",
    "resolve":         "governance",
}

_BASE = {
    "financial": 60, "home_control": 45, "communication": 40,
    "governance": 20, "other": 15, "read": 5,
}


def category(action: str) -> str:
    return CATEGORY.get(action, "other")


def score(action: str, args: dict, decision: str) -> int:
    """A 0-100 score: category base + money exposure + the gate's reaction."""
    args = args or {}
    cat = category(action)
    s = _BASE.get(cat, 15)

    if cat == "financial":
        amount = 0.0
        for k in ("amount", "cost", "price", "total"):
            try:
                amount = float(args.get(k))
                break
            except (TypeError, ValueError):
                continue
        s += min(amount / 20.0, 40)  # $800 -> +40 (capped)

    # The gate's reaction is a signal: a deny/hold means it was worth stopping.
    if decision == "deny":
        s += 20
    elif decision == "hold":
        s += 10

    return int(max(0, min(s, 100)))
