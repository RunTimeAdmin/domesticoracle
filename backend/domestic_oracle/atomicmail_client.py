"""
AtomicMail JMAP client for Domestic Oracle.

Auth chain (three-tier, matching the CLI):
  1. ~/.atomicmail/capability.jwt   — short-lived (~60 min); used directly for JMAP
  2. ~/.atomicmail/session.jwt      — longer-lived; POST /api/v1/capability to refresh
  3. PoW (scrypt) + apiKey          — last resort when session also expires

The CLI writes all three files on first registration and refreshes them automatically.
This client reuses whatever the CLI left behind and only does PoW as a fallback.

Credentials file: ~/.atomicmail/credentials.json
  Required fields: apiKey, inboxId, authUrl, apiUrl, scryptSalt

Override path:
  ORA_ATOMICMAIL_CREDENTIALS_PATH env var

References: RFC 8620 (JMAP Core), RFC 8621 (JMAP Mail)
"""
import hashlib
import json
import os
import time
from pathlib import Path

import httpx

_ATOMICMAIL_DIR   = Path(os.environ.get("ORA_ATOMICMAIL_DIR", str(Path.home() / ".atomicmail")))
_CREDS_PATH       = _ATOMICMAIL_DIR / "credentials.json"
_SESSION_JWT_PATH = _ATOMICMAIL_DIR / "session.jwt"
_CAP_JWT_PATH     = _ATOMICMAIL_DIR / "capability.jwt"

# Module-level caches; keyed per process lifetime.
_jmap_session_cache: dict | None = None
_jmap_session_ts: float = 0.0
_JMAP_SESSION_TTL = 3600.0

_cached_cap_jwt: str = ""
_cached_cap_exp: float = 0.0


class AtomicMailError(Exception):
    pass


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

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


def _jwt_exp(token: str) -> float:
    """Return the exp claim from a JWT, or 0 if unreadable."""
    try:
        import base64
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.b64decode(payload)).get("exp", 0))
    except Exception:
        return 0.0


def _read_jwt_file(path: Path) -> str:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""


# ---------------------------------------------------------------------------
# PoW (scrypt) — last-resort auth when both JWTs have expired
# ---------------------------------------------------------------------------

def _scrypt_hash(data: str, salt: str) -> bytes:
    return hashlib.scrypt(
        data.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=16384, r=8, p=1,
        dklen=64,
    )


def _leading_zero_bits(h: bytes, bits: int) -> bool:
    full, rem = divmod(bits, 8)
    for b in h[:full]:
        if b != 0:
            return False
    if rem:
        mask = (0xFF << (8 - rem)) & 0xFF
        if h[full] & mask:
            return False
    return True


def _solve_pow(challenge: str, difficulty: int, salt: str) -> tuple[str, str]:
    nonce = 0
    while True:
        digest = _scrypt_hash(f"{challenge}:{nonce}", salt)
        if _leading_zero_bits(digest, difficulty):
            return digest.hex(), str(nonce)
        nonce += 1


def _perform_pow_session(creds: dict) -> str:
    """Full PoW flow → returns a fresh session JWT (and saves it)."""
    auth_url = creds["authUrl"]

    # 1. GET challenge
    try:
        r = httpx.post(f"{auth_url}/api/v1/challenge", timeout=10)
        r.raise_for_status()
    except Exception as e:
        raise AtomicMailError(f"Auth challenge failed: {e}") from e

    bearer = r.headers.get("Authorization", "")
    if not bearer.startswith("Bearer "):
        raise AtomicMailError("Challenge response missing Bearer token")
    challenge_jwt = bearer[7:]

    import base64
    pad = challenge_jwt.split(".")[1]
    pad += "=" * (-len(pad) % 4)
    payload = json.loads(base64.b64decode(pad))
    challenge  = payload["jti"]
    difficulty = int(payload["difficulty"])

    # 2. Solve PoW
    pow_hex, nonce = _solve_pow(challenge, difficulty, creds["scryptSalt"])

    # 3. Exchange session
    try:
        r2 = httpx.post(
            f"{auth_url}/api/v1/session",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {challenge_jwt}"},
            content=json.dumps({
                "powHex":   pow_hex,
                "nonce":    nonce,
                "apiKey":   creds["apiKey"],
                "username": creds.get("inboxId", "oracle"),
            }),
            timeout=15,
        )
        r2.raise_for_status()
    except Exception as e:
        raise AtomicMailError(f"Session exchange failed: {e}") from e

    bearer2 = r2.headers.get("Authorization", "")
    if not bearer2.startswith("Bearer "):
        raise AtomicMailError("Session response missing Bearer token")
    session_jwt = bearer2[7:]
    _SESSION_JWT_PATH.write_text(session_jwt)
    return session_jwt


# ---------------------------------------------------------------------------
# Capability JWT (the token used for all JMAP calls)
# ---------------------------------------------------------------------------

def _get_capability_jwt(creds: dict) -> str:
    """Return a valid capability JWT, refreshing via session or PoW as needed."""
    global _cached_cap_jwt, _cached_cap_exp

    now = time.time()

    # 1. In-memory cache
    if _cached_cap_jwt and _cached_cap_exp > now + 30:
        return _cached_cap_jwt

    # 2. File cache
    cap_jwt = _read_jwt_file(_CAP_JWT_PATH)
    if cap_jwt and _jwt_exp(cap_jwt) > now + 30:
        _cached_cap_jwt = cap_jwt
        _cached_cap_exp = _jwt_exp(cap_jwt)
        return cap_jwt

    # 3. Refresh using session JWT
    session_jwt = _read_jwt_file(_SESSION_JWT_PATH)
    if session_jwt and _jwt_exp(session_jwt) > now + 30:
        cap_jwt = _refresh_capability(creds, session_jwt)
        if cap_jwt:
            return cap_jwt

    # 4. Full PoW flow
    session_jwt = _perform_pow_session(creds)
    cap_jwt = _refresh_capability(creds, session_jwt)
    if not cap_jwt:
        raise AtomicMailError("Failed to obtain a capability JWT")
    return cap_jwt


def _refresh_capability(creds: dict, session_jwt: str) -> str:
    global _cached_cap_jwt, _cached_cap_exp
    try:
        r = httpx.post(
            f"{creds['authUrl']}/api/v1/capability",
            headers={"Authorization": f"Bearer {session_jwt}"},
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        raise AtomicMailError(f"Capability refresh failed: {e}") from e

    bearer = r.headers.get("Authorization", "")
    if not bearer.startswith("Bearer "):
        raise AtomicMailError("Capability response missing Bearer token")
    cap_jwt = bearer[7:]
    _CAP_JWT_PATH.write_text(cap_jwt)
    _cached_cap_jwt = cap_jwt
    _cached_cap_exp = _jwt_exp(cap_jwt)
    return cap_jwt


# ---------------------------------------------------------------------------
# JMAP session and request
# ---------------------------------------------------------------------------

def _get_jmap_session(cap_jwt: str, api_url: str) -> dict:
    global _jmap_session_cache, _jmap_session_ts
    now = time.monotonic()
    if _jmap_session_cache and (now - _jmap_session_ts) < _JMAP_SESSION_TTL:
        return _jmap_session_cache
    try:
        r = httpx.get(
            f"{api_url.rstrip('/')}/.well-known/jmap",
            headers={"Authorization": f"Bearer {cap_jwt}"},
            timeout=10,
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise AtomicMailError(f"JMAP session fetch failed ({e.response.status_code})") from e
    except httpx.RequestError as e:
        raise AtomicMailError(f"JMAP session error: {e}") from e
    _jmap_session_cache = r.json()
    _jmap_session_ts = now
    return _jmap_session_cache


def _jmap(method_calls: list, creds: dict) -> dict:
    """POST a JMAP request; returns {methodCallId: result} mapping."""
    cap_jwt = _get_capability_jwt(creds)
    session = _get_jmap_session(cap_jwt, creds["apiUrl"])
    jmap_url = session.get("apiUrl", f"{creds['apiUrl'].rstrip('/')}/jmap/")
    account_id = creds.get("inboxId", "oracle")

    payload = {
        "using": [
            "urn:ietf:params:jmap:core",
            "urn:ietf:params:jmap:mail",
            "urn:ietf:params:jmap:submission",
        ],
        "methodCalls": method_calls,
    }
    try:
        r = httpx.post(
            jmap_url,
            headers={"Authorization": f"Bearer {cap_jwt}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise AtomicMailError(f"JMAP request failed ({e.response.status_code}): {e.response.text[:200]}") from e
    except httpx.RequestError as e:
        raise AtomicMailError(f"JMAP request error: {e}") from e

    return {row[2]: row[1] for row in r.json().get("methodResponses", [])}


_inbox_mailbox_id: str = ""


def _get_inbox_mailbox_id(creds: dict, account_id: str) -> str:
    global _inbox_mailbox_id
    if _inbox_mailbox_id:
        return _inbox_mailbox_id
    responses = _jmap([
        ["Mailbox/query", {
            "accountId": account_id,
            "filter":    {"role": "inbox"},
        }, "mq"],
    ], creds)
    ids = responses.get("mq", {}).get("ids", [])
    if ids:
        _inbox_mailbox_id = ids[0]
    return _inbox_mailbox_id


def _from_addr(creds: dict) -> str:
    inbox_id = creds.get("inboxId") or "oracle"
    return creds.get("email") or f"{inbox_id}@atomicmail.ai"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send(to: str, subject: str, body: str) -> str:
    """Send an email. Returns a confirmation string or raises AtomicMailError."""
    creds = _load_credentials()
    account_id = creds.get("inboxId", "oracle")
    from_addr  = _from_addr(creds)

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
    ], creds)

    sub = responses.get("sub", {})
    if sub.get("created"):
        return f"Email sent to {to}."
    raise AtomicMailError(f"Send failed: {sub.get('notCreated') or sub.get('error')}")


def list_inbox(limit: int = 10) -> list[dict]:
    """Return up to `limit` recent inbox messages as dicts with id/subject/from/preview."""
    creds = _load_credentials()
    account_id = creds.get("inboxId", "oracle")
    inbox_id = _get_inbox_mailbox_id(creds, account_id)

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
    ], creds)

    return responses.get("emails", {}).get("list", [])


def reply(email_id: str, body: str) -> str:
    """Reply to an email by ID. Returns confirmation or raises AtomicMailError."""
    creds = _load_credentials()
    account_id = creds.get("inboxId", "oracle")
    from_addr  = _from_addr(creds)

    info = _jmap([
        ["Email/get", {
            "accountId":  account_id,
            "ids":        [email_id],
            "properties": ["subject", "from", "messageId"],
        }, "orig"],
    ], creds)

    original = (info.get("orig", {}).get("list") or [{}])[0]
    to_addr   = (original.get("from") or [{}])[0].get("email", "")
    subject   = original.get("subject", "")
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
    ], creds)

    sub = responses.get("sub", {})
    if sub.get("created"):
        return f"Reply sent to {to_addr}."
    raise AtomicMailError(f"Reply failed: {sub.get('notCreated') or sub.get('error')}")


def search(query: str, limit: int = 10) -> list[dict]:
    """Search emails by text. Returns matching message dicts."""
    creds = _load_credentials()
    account_id = creds.get("inboxId", "oracle")

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
    ], creds)

    return responses.get("emails", {}).get("list", [])
