"""
AtomicMail JMAP client for Domestic Oracle.

Reads credentials from ~/.atomicmail/credentials.json (written by the AtomicMail CLI
on first registration). Makes authenticated JMAP requests to the AtomicMail API.

One-time setup:
    npx --package=@atomicmail/agent-skill atomicmail register --username <name>

Environment override:
    ORA_ATOMICMAIL_CREDENTIALS_PATH=/path/to/credentials.json

References: RFC 8620 (JMAP Core), RFC 8621 (JMAP Mail)
"""
import json
import os
import time
from pathlib import Path

import httpx

_CREDS_PATH = Path(os.environ.get(
    "ORA_ATOMICMAIL_CREDENTIALS_PATH",
    str(Path.home() / ".atomicmail" / "credentials.json"),
))

_session_cache: dict | None = None
_session_ts: float = 0.0
_inbox_id_cache: str = ""
_SESSION_TTL = 3600.0


class AtomicMailError(Exception):
    pass


def _load_credentials() -> dict:
    try:
        return json.loads(_CREDS_PATH.read_text())
    except FileNotFoundError:
        raise AtomicMailError(
            f"AtomicMail credentials not found at {_CREDS_PATH}. "
            "Register first: "
            "npx --package=@atomicmail/agent-skill atomicmail register --username <name>"
        )
    except Exception as e:
        raise AtomicMailError(f"Failed to read AtomicMail credentials: {e}") from e


def _auth_headers(creds: dict) -> dict:
    token = creds.get("token") or creds.get("api_key") or creds.get("password", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get_session_and_account(creds: dict) -> tuple[dict, str]:
    global _session_cache, _session_ts
    now = time.monotonic()
    if _session_cache and (now - _session_ts) < _SESSION_TTL:
        account_id = next(iter(_session_cache.get("accounts", {}).keys()), "")
        return _session_cache, account_id

    server = creds.get("server", "https://api.atomicmail.ai")
    url = f"{server.rstrip('/')}/.well-known/jmap"
    try:
        r = httpx.get(url, headers=_auth_headers(creds), timeout=10)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise AtomicMailError(f"JMAP session discovery failed ({e.response.status_code})") from e
    except httpx.RequestError as e:
        raise AtomicMailError(f"JMAP session discovery error: {e}") from e

    _session_cache = r.json()
    _session_ts = now
    account_id = next(iter(_session_cache.get("accounts", {}).keys()), "")
    return _session_cache, account_id


def _jmap(method_calls: list, creds: dict, session: dict) -> dict:
    """POST a JMAP request; returns {methodCallId: result} mapping."""
    api_url = session.get("apiUrl", "")
    payload = {
        "using": [
            "urn:ietf:params:jmap:core",
            "urn:ietf:params:jmap:mail",
            "urn:ietf:params:jmap:submission",
        ],
        "methodCalls": method_calls,
    }
    try:
        r = httpx.post(api_url, headers=_auth_headers(creds), json=payload, timeout=15)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise AtomicMailError(f"JMAP request failed ({e.response.status_code}): {e.response.text[:200]}") from e
    except httpx.RequestError as e:
        raise AtomicMailError(f"JMAP request error: {e}") from e

    return {row[2]: row[1] for row in r.json().get("methodResponses", [])}


def _get_inbox_id(creds: dict, session: dict, account_id: str) -> str:
    global _inbox_id_cache
    if _inbox_id_cache:
        return _inbox_id_cache
    responses = _jmap([
        ["Mailbox/get", {
            "accountId": account_id,
            "ids": None,
            "properties": ["id", "role"],
        }, "mbx"],
    ], creds, session)
    for mb in responses.get("mbx", {}).get("list", []):
        if mb.get("role") == "inbox":
            _inbox_id_cache = mb["id"]
            return _inbox_id_cache
    return ""


def _from_addr(creds: dict) -> str:
    username = creds.get("username", "oracle")
    return creds.get("email") or f"{username}@atomicmail.ai"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send(to: str, subject: str, body: str) -> str:
    """Send an email. Returns a confirmation string or raises AtomicMailError."""
    creds = _load_credentials()
    session, account_id = _get_session_and_account(creds)
    from_addr = _from_addr(creds)

    responses = _jmap([
        ["Email/set", {
            "accountId": account_id,
            "create": {
                "draft1": {
                    "from":       [{"email": from_addr}],
                    "to":         [{"email": to}],
                    "subject":    subject,
                    "keywords":   {"$draft": True},
                    "bodyValues": {"body1": {"value": body, "charset": "utf-8"}},
                    "textBody":   [{"partId": "body1", "type": "text/plain"}],
                }
            },
        }, "email"],
        ["EmailSubmission/set", {
            "accountId": account_id,
            "create": {
                "sub1": {
                    "emailId": "#draft1",
                    "envelope": {
                        "mailFrom": {"email": from_addr},
                        "rcptTo":   [{"email": to}],
                    },
                },
            },
        }, "sub"],
    ], creds, session)

    sub = responses.get("sub", {})
    if sub.get("created"):
        return f"Email sent to {to}."
    raise AtomicMailError(f"Send failed: {sub.get('notCreated') or sub.get('error')}")


def list_inbox(limit: int = 10) -> list[dict]:
    """Return up to `limit` recent inbox messages as dicts with id/subject/from/preview."""
    creds = _load_credentials()
    session, account_id = _get_session_and_account(creds)
    inbox_id = _get_inbox_id(creds, session, account_id)

    filter_arg: dict = {"inMailbox": inbox_id} if inbox_id else {}
    responses = _jmap([
        ["Email/query", {
            "accountId": account_id,
            "filter":    filter_arg,
            "sort":      [{"property": "receivedAt", "isAscending": False}],
            "limit":     limit,
        }, "q"],
        ["Email/get", {
            "accountId":  account_id,
            "#ids":        {"resultOf": "q", "name": "Email/query", "path": "/ids"},
            "properties": ["id", "subject", "from", "receivedAt", "preview"],
        }, "emails"],
    ], creds, session)

    return responses.get("emails", {}).get("list", [])


def reply(email_id: str, body: str) -> str:
    """Reply to an email. Returns a confirmation string or raises AtomicMailError."""
    creds = _load_credentials()
    session, account_id = _get_session_and_account(creds)
    from_addr = _from_addr(creds)

    info = _jmap([
        ["Email/get", {
            "accountId":  account_id,
            "ids":        [email_id],
            "properties": ["subject", "from", "messageId"],
        }, "orig"],
    ], creds, session)

    original = (info.get("orig", {}).get("list") or [{}])[0]
    to_addr = (original.get("from") or [{}])[0].get("email", "")
    subject = original.get("subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    in_reply_to = (original.get("messageId") or [""])[0]

    responses = _jmap([
        ["Email/set", {
            "accountId": account_id,
            "create": {
                "reply1": {
                    "from":       [{"email": from_addr}],
                    "to":         [{"email": to_addr}],
                    "subject":    subject,
                    "inReplyTo":  [in_reply_to] if in_reply_to else [],
                    "keywords":   {"$draft": True},
                    "bodyValues": {"body1": {"value": body, "charset": "utf-8"}},
                    "textBody":   [{"partId": "body1", "type": "text/plain"}],
                }
            },
        }, "email"],
        ["EmailSubmission/set", {
            "accountId": account_id,
            "create": {
                "sub1": {
                    "emailId": "#reply1",
                    "envelope": {
                        "mailFrom": {"email": from_addr},
                        "rcptTo":   [{"email": to_addr}],
                    },
                },
            },
        }, "sub"],
    ], creds, session)

    sub = responses.get("sub", {})
    if sub.get("created"):
        return f"Reply sent to {to_addr}."
    raise AtomicMailError(f"Reply failed: {sub.get('notCreated') or sub.get('error')}")


def search(query: str, limit: int = 10) -> list[dict]:
    """Search emails by text. Returns matching message dicts."""
    creds = _load_credentials()
    session, account_id = _get_session_and_account(creds)

    responses = _jmap([
        ["Email/query", {
            "accountId": account_id,
            "filter":    {"text": query},
            "sort":      [{"property": "receivedAt", "isAscending": False}],
            "limit":     limit,
        }, "q"],
        ["Email/get", {
            "accountId":  account_id,
            "#ids":        {"resultOf": "q", "name": "Email/query", "path": "/ids"},
            "properties": ["id", "subject", "from", "receivedAt", "preview"],
        }, "emails"],
    ], creds, session)

    return responses.get("emails", {}).get("list", [])
