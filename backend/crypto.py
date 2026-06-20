"""
Cryptographic primitives for Domestic Oracle.

Ed25519 signatures for the ledger server key and per-agent keypairs.
Nonces are persisted to SQLite so replay protection survives restarts and
is visible across multiple workers.

Key rotation: oracle_keys/keyset.json tracks all public keys ever used so that
historical ledger entries remain verifiable after the active key is rotated.
"""
import json, os, time, sqlite3, secrets
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat, PrivateFormat, NoEncryption,
)
from cryptography.exceptions import InvalidSignature

REQUEST_TTL_SECONDS = 300  # 5 minutes freshness window

_DATA_DIR    = os.environ.get("ORA_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
_KEY_DIR     = os.path.join(_DATA_DIR, "oracle_keys")
_KEY_FILE    = os.path.join(_KEY_DIR, "server_ed25519.hex")
_KEYSET_FILE = os.path.join(_KEY_DIR, "keyset.json")
_DB_PATH     = os.path.join(_DATA_DIR, "oracle.db")

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


# ----------------------------------------------------------------- keyset (rotation)

def _load_keyset() -> list[dict]:
    if not os.path.exists(_KEYSET_FILE):
        return []
    with open(_KEYSET_FILE) as f:
        return json.load(f)


def _save_keyset(keyset: list[dict]) -> None:
    os.makedirs(_KEY_DIR, exist_ok=True)
    with open(_KEYSET_FILE, "w") as f:
        json.dump(keyset, f, indent=2)


def _ensure_keyset() -> list[dict]:
    """Create keyset.json for existing deployments that pre-date key rotation."""
    keyset = _load_keyset()
    if not keyset:
        _load_or_create_server_key()  # ensure key file exists first
        pub = server_public_key_hex()
        rotated_in = (
            os.path.getmtime(_KEY_FILE) if os.path.exists(_KEY_FILE) else time.time()
        )
        keyset = [{"pub_hex": pub, "rotated_in": rotated_in, "rotated_out": None, "active": True}]
        _save_keyset(keyset)
    return keyset


def all_public_keys() -> list[str]:
    """All public keys ever used, current first. Used by verify_chain() fallback."""
    current = server_public_key_hex()
    result = [current]
    for entry in _load_keyset():
        if entry["pub_hex"] not in result:
            result.append(entry["pub_hex"])
    return result


def keyset_info() -> dict:
    """Key status for the /keys/status endpoint — public keys only, never private."""
    _ensure_keyset()
    keyset = _load_keyset()
    active = next((e for e in keyset if e.get("active")), None)
    return {
        "current_pub_hex": server_public_key_hex(),
        "rotation_count": sum(1 for e in keyset if not e.get("active")),
        "active_since": active["rotated_in"] if active else None,
        "history": [
            {
                "pub_hex": e["pub_hex"],
                "rotated_in": e["rotated_in"],
                "rotated_out": e.get("rotated_out"),
                "active": bool(e.get("active")),
            }
            for e in keyset
        ],
    }


def rotate_server_key() -> dict:
    """Generate a new Ed25519 key and retire the current one.

    Historical entries signed by the old key remain verifiable: the old public key
    is preserved in keyset.json and tried as a fallback during chain verification.
    The old PRIVATE key is discarded — only the public key is needed for future
    verification of past entries.
    """
    global _server_key
    _ensure_keyset()
    keyset = _load_keyset()
    old_pub = server_public_key_hex()
    now = time.time()

    for entry in keyset:
        if entry.get("active"):
            entry["active"] = False
            entry["rotated_out"] = now

    new_key = Ed25519PrivateKey.generate()
    new_raw = new_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    new_pub = new_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    os.makedirs(_KEY_DIR, exist_ok=True)
    tmp = _KEY_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(new_raw.hex())
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, _KEY_FILE)

    _server_key = new_key
    keyset.append({"pub_hex": new_pub, "rotated_in": now, "rotated_out": None, "active": True})
    _save_keyset(keyset)

    return {
        "new_pub_hex": new_pub,
        "retired_pub_hex": old_pub,
        "rotated_at": now,
        "rotation_count": sum(1 for e in keyset if not e.get("active")),
    }


def export_private_key_hex() -> str:
    """Return current private key as hex for offline backup.

    Store the result securely (password manager or encrypted file). Anyone
    with this key can forge ledger signatures for NEW entries — existing
    entries are protected by the hash chain linkage.
    """
    key = _load_or_create_server_key()
    return key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()


# ----------------------------------------------------------------- signing helpers

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
