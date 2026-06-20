"""
Add the backend directory to sys.path so pytest can import the modules
directly (ledger, consent, policy, crypto, limits, db) without an installed package.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_connect(db_path):
    """Return a _connect()-shaped callable wired to a temp SQLite file."""
    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    return _connect


@pytest.fixture()
def fresh_key(monkeypatch):
    """Ephemeral Ed25519 key injected via ORA_LEDGER_KEY; resets crypto module state.

    Shared across all test files via conftest auto-discovery. Local fixtures in
    test_governance.py and test_safety.py shadow this for their own modules.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, NoEncryption,
    )
    import crypto
    key = Ed25519PrivateKey.generate()
    key_hex = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()
    monkeypatch.setenv("ORA_LEDGER_KEY", key_hex)
    monkeypatch.setattr(crypto, "_server_key", None)


@pytest.fixture()
def gov_db(tmp_path, monkeypatch, fresh_key):
    """Isolated crypto environment: key files + nonce DB redirected to tmp_path.

    Required for tests that call rotate_server_key(), consume_nonce(), or
    keyset_info() — anything that reads/writes files outside the test process.
    Depends on fresh_key so ORA_LEDGER_KEY is already set and _server_key cleared.
    """
    import crypto as _crypto

    key_dir = tmp_path / "oracle_keys"
    key_dir.mkdir()
    db_file = tmp_path / "oracle.db"

    monkeypatch.setattr(_crypto, "_KEY_DIR",     str(key_dir))
    monkeypatch.setattr(_crypto, "_KEY_FILE",    str(key_dir / "server_ed25519.hex"))
    monkeypatch.setattr(_crypto, "_KEYSET_FILE", str(key_dir / "keyset.json"))
    monkeypatch.setattr(_crypto, "_DB_PATH",     str(db_file))

    # Create the nonces table so consume_nonce() can INSERT without setup.
    with sqlite3.connect(str(db_file)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS nonces "
            "(nonce TEXT PRIMARY KEY, expires_at REAL NOT NULL)"
        )
        conn.commit()

    yield db_file
