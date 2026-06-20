"""
Key rotation and multi-key chain verification.

When the server key is rotated, previously signed ledger entries must still
verify — because crypto._verify_entry_sig() tries the current key first, then
each retired key listed in keyset.json. This test verifies that invariant
across the rotation boundary.

Two contracts:
  entries_verify_after_rotation — entries signed by the old key remain valid
                                  after the new key is active
  retired_key_in_all_public_keys — the retired public key is accessible for
                                   independent out-of-band verification

Key file paths are redirected to an isolated temp directory so the test never
touches the production oracle_keys/ directory.
"""
import sqlite3

import pytest

import crypto
import ledger


def _make_connect(db_path):
    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    return _connect


@pytest.fixture()
def rotation_env(tmp_path, monkeypatch):
    """Isolated key files + ledger DB for key-rotation tests.

    The production oracle_keys/ directory is NEVER touched. All key material
    written during this test lands in tmp_path/oracle_keys/.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, NoEncryption,
    )

    # Redirect crypto's key-file paths to the temp directory.
    key_dir  = tmp_path / "oracle_keys"
    key_dir.mkdir()
    monkeypatch.setattr(crypto, "_KEY_DIR",     str(key_dir))
    monkeypatch.setattr(crypto, "_KEY_FILE",    str(key_dir / "server_ed25519.hex"))
    monkeypatch.setattr(crypto, "_KEYSET_FILE", str(key_dir / "keyset.json"))
    monkeypatch.setattr(crypto, "_DB_PATH",     str(tmp_path / "oracle.db"))
    # Clear cached key so _load_or_create_server_key() re-reads from ORA_LEDGER_KEY.
    monkeypatch.setattr(crypto, "_server_key", None)

    # Inject a fresh Ed25519 key as the starting key.
    key1 = Ed25519PrivateKey.generate()
    key1_hex = key1.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()
    monkeypatch.setenv("ORA_LEDGER_KEY", key1_hex)

    # Isolated ledger DB.
    db_file    = tmp_path / "oracle.db"
    anchor_log = tmp_path / "anchor.log"
    connect    = _make_connect(db_file)
    monkeypatch.setattr(ledger, "_connect", connect)
    monkeypatch.setattr(ledger, "ANCHOR_FILE", str(anchor_log))
    monkeypatch.setattr(ledger, "_verify_checkpoint", {"id": 0, "hash": ledger.GENESIS_HASH})
    ledger.init_db()

    yield db_file, key1_hex


# ── rotation tests ────────────────────────────────────────────────────────────

def test_pre_rotation_entries_verify_after_rotation(rotation_env):
    """Entries signed by the old key still verify after the server key is rotated.

    _verify_entry_sig() tries the active key first, then falls back to each
    retired key in keyset.json.  This test exercises that fallback path:
      - two entries written with key1
      - key is rotated → new key2 becomes active
      - two more entries written with key2
      - verify_chain(full=True) must pass for all four entries
    """
    db_file, key1_hex = rotation_env

    # Write entries with key1.
    e1 = ledger.append("ora.core", "action_a", "arg=1", "allow", "executed")
    e2 = ledger.append("ora.core", "action_b", "arg=2", "allow", "executed")

    pre = ledger.verify_chain(full=True)
    assert pre["valid"], f"Pre-rotation chain must be intact: {pre}"
    assert pre["checked"] == 2

    pub_before = crypto.server_public_key_hex()

    # Rotate: generates key2, retires key1's pub to keyset.json.
    rotation = crypto.rotate_server_key()
    assert rotation["new_pub_hex"] != rotation["retired_pub_hex"]
    assert rotation["retired_pub_hex"] == pub_before

    # Write entries with key2.
    e3 = ledger.append("ora.core", "action_c", "arg=3", "allow", "executed")
    e4 = ledger.append("ora.core", "action_d", "arg=4", "allow", "executed")

    # Full chain verification must span the key-rotation boundary.
    post = ledger.verify_chain(full=True)
    assert post["valid"], (
        f"Post-rotation chain must verify via retired key fallback: {post}"
    )
    assert post["checked"] == 4, f"Expected 4 entries; got {post['checked']}"


def test_retired_key_in_all_public_keys(rotation_env):
    """After rotation, all_public_keys() includes the retired key for out-of-band verification."""
    _, _ = rotation_env

    ledger.append("ora.core", "first_action", "arg=1", "allow", "executed")
    old_pub = crypto.server_public_key_hex()

    crypto.rotate_server_key()
    new_pub = crypto.server_public_key_hex()

    all_pubs = crypto.all_public_keys()
    assert new_pub in all_pubs, "Active key must appear in all_public_keys()"
    assert old_pub in all_pubs, "Retired key must remain in all_public_keys() for verification"
    # Current key is listed first by convention.
    assert all_pubs[0] == new_pub


def test_keyset_rotation_count_increments(rotation_env):
    """keyset_info().rotation_count increments with each rotation."""
    _, _ = rotation_env

    before = crypto.keyset_info()
    assert before["rotation_count"] == 0

    crypto.rotate_server_key()
    after = crypto.keyset_info()
    assert after["rotation_count"] == 1

    crypto.rotate_server_key()
    after2 = crypto.keyset_info()
    assert after2["rotation_count"] == 2
