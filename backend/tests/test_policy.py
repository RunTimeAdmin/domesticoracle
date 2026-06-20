"""
Policy rule-type and enforcement-mode tests.

One test per rule type, plus enforcement-mode contracts:
  spend_limit     — purchases over max_amount are HOLD; under the cap are ALLOW
  time_window     — actions inside the configured hour window are HOLD
  action_deny     — matched action type produces DENY (not HOLD)
  recipient_block — send_message to matched recipient is HOLD; all_contacts blocks anyone
  deny_beats_hold — strictest verdict wins when multiple rules fire on the same action

Enforcement modes:
  enforced (default) — holds and denies are enforced
  audit_only         — would-hold action still executes; ledger records the observation
  permissive         — would-deny action still executes
  mode switch        — switching back to enforced re-enables blocking

Each test runs with ACTOR_HOURLY_LIMIT=0 and DAILY_CAP=0 so only policy
decisions drive the outcome. The consent gate is used as the entry point rather
than policy.evaluate() directly, because that is the product surface under test.
"""
import sqlite3

import pytest

import consent
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
def policy_db(tmp_path, monkeypatch, fresh_key):
    """Isolated DB with all tables; seed default policies cleared.

    Disabling seed defaults lets each test install exactly the rule it needs
    without having to account for the three seeded rules overlapping.
    """
    db_file = tmp_path / "oracle.db"
    anchor_file = tmp_path / "anchor.log"

    connect = _make_connect(db_file)
    monkeypatch.setattr(ledger,   "_connect", connect)
    monkeypatch.setattr(policy,   "_connect", connect)
    monkeypatch.setattr(consent,  "_connect", connect)
    monkeypatch.setattr(limits,   "_connect", connect)
    monkeypatch.setattr(ledger,   "ANCHOR_FILE", str(anchor_file))
    monkeypatch.setattr(ledger,   "_verify_checkpoint", {"id": 0, "hash": ledger.GENESIS_HASH})
    monkeypatch.setattr(policy,   "_policy_cache", None)
    consent._executor = None
    consent.init_db()

    # Remove seed defaults so each test controls exactly which rules are active.
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute("DELETE FROM policies")
        conn.commit()
    monkeypatch.setattr(policy, "_policy_cache", None)

    yield db_file


def _run(action: str, args: dict):
    """Run through consent gate and return (result, executed_flag_list)."""
    executed: list[bool] = []
    result = consent.request_action(
        actor_id="ora.core",
        action=action,
        args=args,
        execute=lambda: executed.append(True) or "ok",
    )
    return result, executed


def _disable_rate_limits(monkeypatch):
    monkeypatch.setattr(limits, "ACTOR_HOURLY_LIMIT", 0)
    monkeypatch.setattr(limits, "DAILY_CAP", 0)


# ── spend_limit ───────────────────────────────────────────────────────────────

def test_spend_limit_over_cap_is_hold(policy_db, monkeypatch):
    _disable_rate_limits(monkeypatch)
    policy.add_policy("spend_limit", {"max_amount": 20}, label="$20 cap")

    result, executed = _run("make_purchase", {"item": "book", "amount": 30.00})
    assert result["status"] == "held", f"Expected 'held'; got {result}"
    assert not executed


def test_spend_limit_under_cap_is_allow(policy_db, monkeypatch):
    _disable_rate_limits(monkeypatch)
    policy.add_policy("spend_limit", {"max_amount": 100}, label="$100 cap")

    result, executed = _run("make_purchase", {"item": "pen", "amount": 5.00})
    assert result["status"] == "executed", f"Expected 'executed'; got {result}"
    assert executed


def test_spend_limit_exact_cap_is_allow(policy_db, monkeypatch):
    """Purchase at exactly the cap value is allowed (condition is strictly >)."""
    _disable_rate_limits(monkeypatch)
    policy.add_policy("spend_limit", {"max_amount": 50}, label="$50 cap")

    result, executed = _run("make_purchase", {"item": "item", "amount": 50.00})
    assert result["status"] == "executed"
    assert executed


# ── time_window ───────────────────────────────────────────────────────────────

class _FakeHour:
    """Thin wrapper so monkeypatch can replace policy.datetime without touching
    the real datetime module elsewhere."""
    def __init__(self, hour: int):
        self._hour = hour

    class datetime:
        pass  # populated per-instance below

    def __class_getitem__(cls, hour):
        import datetime as _dt
        class _MockDatetime(_dt.datetime):
            @classmethod
            def now(klass, tz=None):
                return _dt.datetime(2024, 1, 15, hour, 0, 0)
        return type("_FakeDateTime", (), {"datetime": _MockDatetime})()


def _patch_hour(monkeypatch, hour: int):
    import datetime as _dt
    class _MockDatetime(_dt.datetime):
        @classmethod
        def now(klass, tz=None):
            return _dt.datetime(2024, 1, 15, hour, 0, 0)
    # policy.datetime is the stdlib datetime module; replace its .datetime class.
    monkeypatch.setattr(policy.datetime, "datetime", _MockDatetime)


def test_time_window_inside_window_is_hold(policy_db, monkeypatch):
    """At hour=14, a window covering 8→22 produces HOLD."""
    _disable_rate_limits(monkeypatch)
    _patch_hour(monkeypatch, 14)
    policy.add_policy("time_window", {"start_hour": 8, "end_hour": 22})
    monkeypatch.setattr(policy, "_policy_cache", None)

    result, executed = _run("make_purchase", {"item": "book", "amount": 5.00})
    assert result["status"] == "held", f"Expected 'held' at hour=14 in 8-22 window; got {result}"
    assert not executed


def test_time_window_outside_window_is_allow(policy_db, monkeypatch):
    """At hour=3, a window covering 8→22 produces ALLOW."""
    _disable_rate_limits(monkeypatch)
    _patch_hour(monkeypatch, 3)
    policy.add_policy("time_window", {"start_hour": 8, "end_hour": 22})
    monkeypatch.setattr(policy, "_policy_cache", None)

    result, executed = _run("make_purchase", {"item": "book", "amount": 5.00})
    assert result["status"] == "executed", f"Expected 'executed' at hour=3 outside 8-22; got {result}"
    assert executed


def test_time_window_wraps_midnight(policy_db, monkeypatch):
    """Window 23→6 wraps past midnight; at hour=2 (early morning) the action is held."""
    _disable_rate_limits(monkeypatch)
    _patch_hour(monkeypatch, 2)
    policy.add_policy("time_window", {"start_hour": 23, "end_hour": 6})
    monkeypatch.setattr(policy, "_policy_cache", None)

    result, _ = _run("make_purchase", {"item": "item", "amount": 1.0})
    assert result["status"] == "held", f"Expected 'held' at hour=2 in 23→6 window; got {result}"


# ── action_deny ───────────────────────────────────────────────────────────────

def test_action_deny_produces_deny_not_hold(policy_db, monkeypatch):
    """action_deny blocks the action outright (DENY), distinct from HOLD."""
    _disable_rate_limits(monkeypatch)
    policy.add_policy("action_deny", {"action": "send_message"}, label="No messaging")

    result, executed = _run("send_message", {"recipient": "alice", "body": "hi"})
    assert result["status"] == "denied", f"Expected 'denied'; got {result}"
    assert not executed


def test_action_deny_does_not_affect_other_actions(policy_db, monkeypatch):
    """A deny rule for send_message does not affect make_purchase."""
    _disable_rate_limits(monkeypatch)
    policy.add_policy("action_deny", {"action": "send_message"}, label="No messaging")

    result, executed = _run("make_purchase", {"item": "book", "amount": 5.0})
    assert result["status"] == "executed"
    assert executed


# ── recipient_block ───────────────────────────────────────────────────────────

def test_recipient_block_matched_name_is_hold(policy_db, monkeypatch):
    _disable_rate_limits(monkeypatch)
    policy.add_policy("recipient_block", {"recipient": "alice"}, label="No messages to alice")

    result, executed = _run("send_message", {"recipient": "alice", "body": "hi"})
    assert result["status"] == "held", f"Expected 'held'; got {result}"
    assert not executed


def test_recipient_block_all_contacts_blocks_anyone(policy_db, monkeypatch):
    """The all_contacts sentinel holds messaging ANY named recipient."""
    _disable_rate_limits(monkeypatch)
    policy.add_policy("recipient_block", {"recipient": "all_contacts"})

    for recipient in ("alice", "bob", "unknown@email.com"):
        result, _ = _run("send_message", {"recipient": recipient, "body": "hello"})
        assert result["status"] == "held", (
            f"all_contacts should hold message to {recipient!r}; got {result['status']!r}"
        )


def test_recipient_block_unmatched_name_allows(policy_db, monkeypatch):
    """Message to bob is allowed when only alice is in the block list."""
    _disable_rate_limits(monkeypatch)
    policy.add_policy("recipient_block", {"recipient": "alice"})

    result, executed = _run("send_message", {"recipient": "bob", "body": "hi"})
    assert result["status"] == "executed"
    assert executed


# ── verdict precedence ────────────────────────────────────────────────────────

def test_deny_beats_hold_when_both_match(policy_db, monkeypatch):
    """When both a HOLD rule and a DENY rule match, the DENY verdict wins."""
    _disable_rate_limits(monkeypatch)
    policy.add_policy("spend_limit", {"max_amount": 0}, label="Hold all purchases")
    policy.add_policy("action_deny",  {"action": "make_purchase"}, label="Block purchases")

    result, executed = _run("make_purchase", {"item": "x", "amount": 1.0})
    assert result["status"] == "denied", f"DENY must beat HOLD; got {result['status']!r}"
    assert not executed


# ── enforcement modes ─────────────────────────────────────────────────────────

def test_default_mode_is_enforced(policy_db):
    assert policy.get_mode() == policy.ENFORCED


def test_audit_only_would_hold_still_executes(policy_db, monkeypatch):
    """In audit_only mode a would-HOLD action still runs; ledger records the observation."""
    _disable_rate_limits(monkeypatch)
    policy.add_policy("spend_limit", {"max_amount": 0}, label="Hold all")
    policy.set_mode(policy.AUDIT_ONLY)

    result, executed = _run("make_purchase", {"item": "book", "amount": 10.0})
    assert result["status"] == "executed", (
        f"audit_only: would-hold action must still execute; got {result}"
    )
    assert "audit_only" in result["reason"].lower() or "audit" in result["reason"].lower()
    assert executed


def test_permissive_would_deny_still_executes(policy_db, monkeypatch):
    """In permissive mode a would-DENY action still runs."""
    _disable_rate_limits(monkeypatch)
    policy.add_policy("action_deny", {"action": "make_purchase"}, label="Block purchases")
    policy.set_mode(policy.PERMISSIVE)

    result, executed = _run("make_purchase", {"item": "book", "amount": 5.0})
    assert result["status"] == "executed", (
        f"permissive: would-deny action must still execute; got {result}"
    )
    assert executed


def test_mode_switch_to_enforced_reenables_blocking(policy_db, monkeypatch):
    """After switching from audit_only back to enforced, denies are enforced again."""
    _disable_rate_limits(monkeypatch)
    policy.add_policy("action_deny", {"action": "make_purchase"}, label="Block purchases")

    policy.set_mode(policy.AUDIT_ONLY)
    r1, ex1 = _run("make_purchase", {"item": "pen", "amount": 1.0})
    assert r1["status"] == "executed"  # audit_only lets it through

    policy.set_mode(policy.ENFORCED)
    r2, ex2 = _run("make_purchase", {"item": "pen", "amount": 1.0})
    assert r2["status"] == "denied"    # enforced blocks it
    assert not ex2
