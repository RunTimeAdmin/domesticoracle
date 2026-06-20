"""
Crypto-layer tests: the primitives every other guarantee rests on.

The ledger's tamper-evidence, the agent broker's authentication, and the
anchor's rollback detection all bottom out in crypto.sign/verify, the nonce
store, the freshness window, and key rotation. If these are wrong, every
higher-level test is verifying a lie. So they get tested directly.

Contracts locked in here:

  sign/verify roundtrip            a signature over data verifies with the
                                   matching public key and only that key.
  tamper rejection                 a single flipped byte in the data fails.
  forged-signature rejection       a signature from a different key fails.
  agent keypair                    generate_agent_keypair produces a usable
                                   sign/verify pair distinct from the server key.
  freshness window                 is_fresh accepts now, rejects > TTL old and
                                   far-future timestamps.
  nonce replay                     consume_nonce returns True once, False on the
                                   second use of the same nonce (replay rejected).
  key rotation                     rotate_server_key changes the active key,
                                   preserves the old PUBLIC key in the keyset, and
                                   entries signed by the retired key still verify
                                   via the all_public_keys fallback.
"""
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, NoEncryption, PublicFormat,
)

import crypto

# fresh_key and gov_db come from conftest.py (auto-injected by name).


# --------------------------------------------------------------------------- sign/verify

def test_sign_verify_roundtrip(fresh_key):
    data = b"ledger-entry-hash-abc123"
    sig = crypto.sign(data)
    assert crypto.verify(data, sig) is True
    # And against the explicit public key, which is what verify_chain uses.
    assert crypto.verify(data, sig, crypto.server_public_key_hex()) is True


def test_verify_rejects_tampered_data(fresh_key):
    data = b"transfer 100 dollars to alice"
    sig = crypto.sign(data)
    tampered = b"transfer 900 dollars to alice"
    assert crypto.verify(tampered, sig) is False


def test_verify_rejects_foreign_key_signature(fresh_key):
    """A signature made by some other key must not verify under the server key.

    This is the whole point of signing the ledger: an attacker who recomputes
    the hash chain but signs with their own key produces a forgery that fails.
    """
    data = b"chain-head"
    other = Ed25519PrivateKey.generate()
    foreign_sig = other.sign(data).hex()
    # Verifying the foreign signature under the server's key must fail.
    assert crypto.verify(data, foreign_sig) is False
    # And the legit server signature must NOT verify under the foreign pubkey.
    legit_sig = crypto.sign(data)
    foreign_pub = other.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    assert crypto.verify(data, legit_sig, foreign_pub) is False


def test_verify_handles_garbage_input(fresh_key):
    """Malformed sig/key hex must return False, never raise."""
    data = b"x"
    assert crypto.verify(data, "not-hex-zzzz") is False
    assert crypto.verify(data, "abcd", "also-not-hex") is False
    assert crypto.verify(data, "") is False


# --------------------------------------------------------------------------- agent keypairs

def test_agent_keypair_signs_and_verifies(fresh_key):
    priv_hex, pub_hex = crypto.generate_agent_keypair()
    assert priv_hex != pub_hex
    # The agent's public key must differ from the server key (independent identity).
    assert pub_hex != crypto.server_public_key_hex()

    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
    msg = crypto.agent_request_message("agent.1", "make_purchase",
                                       '{"amount":10}', "nonce1", time.time())
    sig = priv.sign(msg).hex()
    assert crypto.verify(msg, sig, pub_hex) is True
    # A different message under the same key fails.
    other_msg = crypto.agent_request_message("agent.1", "make_purchase",
                                             '{"amount":999}', "nonce1", time.time())
    assert crypto.verify(other_msg, sig, pub_hex) is False


def test_agent_request_message_is_deterministic(fresh_key):
    a = crypto.agent_request_message("a", "act", "{}", "n", 1.5)
    b = crypto.agent_request_message("a", "act", "{}", "n", 1.5)
    assert a == b
    # Any field change changes the bytes (so a swapped field can't be replayed).
    assert crypto.agent_request_message("a", "act", "{}", "n", 1.6) != a


# --------------------------------------------------------------------------- freshness

def test_is_fresh_window():
    now = time.time()
    assert crypto.is_fresh(now) is True
    assert crypto.is_fresh(now - 10) is True
    # Just past the TTL on either side must be stale.
    assert crypto.is_fresh(now - crypto.REQUEST_TTL_SECONDS - 5) is False
    assert crypto.is_fresh(now + crypto.REQUEST_TTL_SECONDS + 5) is False


# --------------------------------------------------------------------------- nonce replay

def test_consume_nonce_blocks_replay(gov_db):
    """First use accepted, exact same nonce rejected the second time."""
    n = "deadbeef" * 4
    assert crypto.consume_nonce(n) is True
    assert crypto.consume_nonce(n) is False  # replay
    # A different nonce is still accepted.
    assert crypto.consume_nonce("cafebabe" * 4) is True


def test_consume_nonce_independent_values(gov_db):
    for i in range(5):
        assert crypto.consume_nonce(f"nonce-{i}") is True
    # Re-using any of them fails.
    for i in range(5):
        assert crypto.consume_nonce(f"nonce-{i}") is False


# --------------------------------------------------------------------------- key rotation

def test_rotate_changes_active_key_and_preserves_history(gov_db):
    """After rotation the active key is new, the old PUBLIC key is retained, and
    something signed before rotation still verifies via all_public_keys()."""
    old_pub = crypto.server_public_key_hex()
    data = b"signed-before-rotation"
    old_sig = crypto.sign(data)

    result = crypto.rotate_server_key()
    new_pub = crypto.server_public_key_hex()

    assert new_pub != old_pub
    assert result["retired_pub_hex"] == old_pub
    assert result["new_pub_hex"] == new_pub

    # The old signature no longer verifies under the CURRENT key...
    assert crypto.verify(data, old_sig) is False
    # ...but the retired public key is still in the keyset, so the ledger's
    # all_public_keys() fallback can still verify historical entries.
    pubs = crypto.all_public_keys()
    assert new_pub in pubs and old_pub in pubs
    assert any(crypto.verify(data, old_sig, p) for p in pubs)


def test_keyset_info_counts_rotations(gov_db):
    info0 = crypto.keyset_info()
    assert info0["rotation_count"] == 0
    crypto.rotate_server_key()
    crypto.rotate_server_key()
    info2 = crypto.keyset_info()
    assert info2["rotation_count"] == 2
    # History never loses keys: 1 active + 2 retired.
    assert len(info2["history"]) == 3
    assert info2["current_pub_hex"] == crypto.server_public_key_hex()


def test_export_private_key_roundtrips(fresh_key):
    """The exported private key reconstructs a key that produces verifiable sigs."""
    priv_hex = crypto.export_private_key_hex()
    reconstructed = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
    data = b"backup-check"
    sig = reconstructed.sign(data).hex()
    assert crypto.verify(data, sig, crypto.server_public_key_hex()) is True
