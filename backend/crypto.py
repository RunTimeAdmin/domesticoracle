"""
Cryptographic primitives for EchoBond.

Ed25519 signatures for the ledger server key and per-agent keypairs.
Nonces are persisted to SQLite so replay protection survives restarts and
is visible across multiple workers — unlike the in-memory dict in Ora.
"""
import os, time, sqlite3, secrets
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat, PrivateFormat, NoEncryption,
)
from cryptography.exceptions import InvalidSignature

REQUEST_TTL_SECONDS = 300  # 5 minutes freshness window

_KEY_DIR  = os.path.join(os.path.dirname(__file__), "oracle_keys")
_KEY_FILE = os.path.join(_KEY_DIR, "server_ed25519.hex")
_DB_PATH  = os.path.join(os.path.dirname(__file__), "oracle.db")

_server_key: Ed25519PrivateKey | None = None


def _load_or_create_server_key() -> Ed25519PrivateKey:
    global _server_key
    if _server_key is not None:
        return _server_key

    env_hex = os.getenv("ORA_LEDGER_KEY", "").strip()
    if env_hex:
        raw = bytes.fromhex(env_hex)
        _server_key = Ed25519PrivateKey.from_private_bytes(raw)
        return _server_key

    os.makedirs(_KEY_DIR, exist_ok=True)
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE) as f:
            raw = bytes.fromhex(f.read().strip())
        _server_key = Ed25519PrivateKey.from_private_bytes(raw)
    else:
        _server_key = Ed25519PrivateKey.generate()
        raw = _server_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        with open(_KEY_FILE, "w") as f:
            f.write(raw.hex())
        try:
            os.chmod(_KEY_FILE, 0o600)
        except OSError:
            pass

    return _server_key


def sign(data: bytes) -> str:
    key = _load_or_create_server_key()
    sig = key.sign(data)
    return sig.hex()


def verify(data: bytes, sig_hex: str, pub_hex: str | None = None) -> bool:
    try:
        if pub_hex:
            pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        else:
            pub = _load_or_create_server_key().public_key()
        pub.verify(bytes.fromhex(sig_hex), data)
        return True
    except (InvalidSignature, ValueError):
        return False


def server_public_key_hex() -> str:
    pub = _load_or_create_server_key().public_key()
    return pub.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def generate_agent_keypair() -> tuple[str, str]:
    """Return (private_hex, public_hex) for a new external agent."""
    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_raw  = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv_raw.hex(), pub_raw.hex()


def new_token() -> str:
    return secrets.token_hex(32)


def is_fresh(ts: float) -> bool:
    return abs(time.time() - ts) < REQUEST_TTL_SECONDS


def agent_request_message(actor_id: str, action: str, args_canonical: str,
                          nonce: str, ts: float) -> bytes:
    payload = f"{actor_id}|{action}|{args_canonical}|{nonce}|{ts}"
    return payload.encode("utf-8")


def consume_nonce(nonce: str) -> bool:
    """Persist nonce to DB. Returns False if already seen (replay rejected).

    Uses SQLite UNIQUE PRIMARY KEY + INSERT to make the check-and-set atomic
    without a separate lock. Expired nonces are pruned on each successful insert.
    """
    now = time.time()
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.execute(
                "INSERT INTO nonces (nonce, expires_at) VALUES (?, ?)",
                (nonce, now + REQUEST_TTL_SECONDS),
            )
            conn.execute("DELETE FROM nonces WHERE expires_at < ?", (now,))
            conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # duplicate nonce — replay rejected
