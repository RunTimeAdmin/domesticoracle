"""
Owner authentication for the control plane.

The whole product rests on one principle: a held action is released only by the human who
owns the home, through a channel the AI cannot drive. So the sensitive endpoints - resolving
approvals, changing policies, revoking agents, registering agents, wiping memory - require an
owner token. The model has no way to present it.

The token is read from ORA_OWNER_TOKEN, or generated once and stored in a key file outside
the database. It's printed to the server log on first run so the owner can copy it into the
frontend. Comparison is constant-time.

This is deliberately simple single-owner auth for a home deployment. Multi-user accounts,
sessions, and OAuth are a later phase; the seam (a FastAPI dependency) is here so they slot in
without touching every endpoint.
"""
import os, hmac, secrets

from fastapi import Header, HTTPException

import crypto

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "oracle_keys", "owner.token")
_token: str | None = None


def owner_token() -> str:
    global _token
    if _token is not None:
        return _token

    env = os.getenv("ORA_OWNER_TOKEN")
    if env:
        _token = env.strip()
        return _token

    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            _token = f.read().strip()
    else:
        _token = crypto.new_token()
        with open(TOKEN_FILE, "w") as f:
            f.write(_token)
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass
        print("\n" + "=" * 64)
        print("  Ora owner token (needed for approvals + policy changes):")
        print(f"  {_token}")
        print("  Set NEXT_PUBLIC_ORA_OWNER_TOKEN to this in the frontend.")
        print("=" * 64 + "\n")
    return _token


def verify_owner(token: str | None) -> bool:
    if not token:
        return False
    return hmac.compare_digest(token.strip(), owner_token())


def require_owner(
    x_ora_owner: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> bool:
    """FastAPI dependency. Accepts the token via X-Ora-Owner or 'Authorization: Bearer'."""
    token = x_ora_owner
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    if not verify_owner(token):
        raise HTTPException(status_code=401, detail="Owner authorization required.")
    return True
