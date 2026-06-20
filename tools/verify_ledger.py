#!/usr/bin/env python3
"""
Domestic Oracle — standalone ledger verifier.

Re-walks the entire hash chain and checks every Ed25519 signature against the
public key embedded in the export. No app dependencies: only the standard library
plus the 'cryptography' package (pip install cryptography).

Usage:
  # 1. Export the chain from a running server:
  #    curl http://localhost:8000/ledger/export > export.json

  # 2. Verify it (no server required after this point):
  python verify_ledger.py export.json

Exit 0 = chain intact. Exit 1 = tampered or invalid.
"""
import json
import sys
import hashlib

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
except ImportError:
    print("Missing dependency: pip install cryptography")
    sys.exit(2)

GENESIS_HASH = "0" * 64


def _canonical_new(e: dict) -> str:
    payload = {
        "ts":           e["ts"],
        "actor_id":     e["actor_id"],
        "action":       e["action"],
        "args_summary": e["args_summary"],
        "args_json":    e.get("args_json", "{}"),
        "decision":     e["decision"],
        "status":       e["status"],
        "outcome":      e["outcome"],
        "prev_hash":    e["prev_hash"],
        "category":     e.get("category", ""),
        "risk":         e.get("risk", 0),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _canonical_legacy(e: dict) -> str:
    payload = {
        "ts":           e["ts"],
        "actor_id":     e["actor_id"],
        "action":       e["action"],
        "args_summary": e["args_summary"],
        "decision":     e["decision"],
        "status":       e["status"],
        "outcome":      e["outcome"],
        "prev_hash":    e["prev_hash"],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _check_hash(e: dict) -> bool:
    """Return True if entry's hash matches either canonical format."""
    for canon in (_canonical_new, _canonical_legacy):
        if hashlib.sha256(canon(e).encode()).hexdigest() == e["hash"]:
            return True
    return False


def _build_pub_keys(data: dict) -> list:
    """Build ordered list of Ed25519PublicKey objects to try for signature checks.

    Exports from server >= key-rotation release include a 'keyset' list covering
    all keys ever used. Older exports have only 'public_key_hex'. We try the
    current key first (most entries), then retired keys in reverse-rotation order
    (most-recently-retired is most likely to match older entries).
    """
    seen: set[str] = set()
    keys: list = []

    current = data.get("public_key_hex", "")
    if current and current not in seen:
        keys.append(Ed25519PublicKey.from_public_bytes(bytes.fromhex(current)))
        seen.add(current)

    for entry in reversed(data.get("keyset", [])):
        ph = entry.get("pub_hex", "")
        if ph and ph not in seen:
            keys.append(Ed25519PublicKey.from_public_bytes(bytes.fromhex(ph)))
            seen.add(ph)

    return keys


def _verify_sig(pub_keys: list, sig_hex: str, hash_str: str) -> bool:
    """Return True if any known public key verifies the signature."""
    for pub in pub_keys:
        try:
            pub.verify(bytes.fromhex(sig_hex), hash_str.encode("utf-8"))
            return True
        except InvalidSignature:
            continue
    return False


def verify(export_path: str) -> bool:
    with open(export_path) as f:
        data = json.load(f)

    pub_keys = _build_pub_keys(data)
    if not pub_keys:
        print("FAIL: export missing 'public_key_hex'")
        return False

    keyset_size = len(data.get("keyset", []))
    if keyset_size > 1:
        print(f"  keyset: {len(pub_keys)} key(s) ({keyset_size - 1} retired)")

    entries = data.get("entries", [])
    if not entries:
        print("OK: 0 entries — empty ledger.")
        return True

    prev_hash = GENESIS_HASH
    for e in entries:
        eid = e["id"]

        # 1. Hash integrity: canonical payload → SHA-256 must match stored hash
        if not _check_hash(e):
            print(f"FAIL  #{eid}  [{e['action']}]: hash mismatch — field was tampered")
            return False

        # 2. Chain linkage: each entry's prev_hash must equal the preceding entry's hash
        if e["prev_hash"] != prev_hash:
            print(f"FAIL  #{eid}  [{e['action']}]: chain break — prev_hash doesn't match")
            return False

        # 3. Ed25519 signature: try all known keys (handles pre-rotation entries)
        if not _verify_sig(pub_keys, e["sig"], e["hash"]):
            print(f"FAIL  #{eid}  [{e['action']}]: signature invalid — forgery or unknown key")
            return False

        prev_hash = e["hash"]
        print(f"  ok  #{eid}  {e['action']}  by {e['actor_id']}")

    current_pub = data.get("public_key_hex", "")
    print(f"\nOK: {len(entries)} {'entry' if len(entries) == 1 else 'entries'} verified. "
          f"Chain is intact.")
    if current_pub:
        print(f"    Active key: {current_pub[:16]}…")
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <export.json>")
        print(f"       Obtain export.json: curl http://localhost:8000/ledger/export > export.json")
        sys.exit(1)

    ok = verify(sys.argv[1])
    sys.exit(0 if ok else 1)
