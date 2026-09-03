"""The envelope boundary. No database: it is pure, and a test that needed
Postgres to check a key derivation would be gated on the wrong thing -
which is exactly how these five first arrived as SKIPS rather than RED.
"""

from __future__ import annotations

import uuid

import pytest

pytest.importorskip("cryptography")

# --- the keys the human actually pasted -----------------------------------
#
# On both Railway services PII_ENCRYPTION_KEY and PII_HASH_KEY are 43-character
# base64url strings - the shape of `secrets.token_urlsafe(32)`. The first
# version of `_key` accepted 64 hex characters or a 32-BYTE utf-8 string and
# raised for anything else, and the Sealer is constructed eagerly: merged as
# it was, the live worker would have refused every job with a config error.
#
# A key format is not something a human can be expected to infer from a
# variable name, so the code bends. The key is DERIVED, not parsed.


TOKEN_URLSAFE_KEY = "wJ8Qx3nB2vK7pL9mR4tY6uI1oP5aS0dF8gH2jK4lZ6c"  # 43 chars


def test_a_token_urlsafe_key_opens_what_it_sealed() -> None:
    from adapter.crypto import Sealer

    sealer = Sealer(encryption_key=TOKEN_URLSAFE_KEY, hash_key=TOKEN_URLSAFE_KEY)
    lead_id = uuid.uuid4()
    sealed = sealer.seal(lead_id, "brief", b"secret")

    assert len(TOKEN_URLSAFE_KEY) == 43
    assert sealer.open(lead_id, "brief", sealed) == b"secret"


def test_one_string_pasted_twice_still_gives_two_different_keys() -> None:
    """The realistic mistake: the same generated value in both variables. The
    derivation binds the variable NAME, so the encryption key and the
    fingerprint key cannot collide even then."""
    from adapter.crypto import derive_key

    same = TOKEN_URLSAFE_KEY
    assert derive_key(same, "PII_ENCRYPTION_KEY") != derive_key(same, "PII_HASH_KEY")


def test_a_key_is_stable_for_a_given_string() -> None:
    """It has to be: a process that derived a different key on restart could
    not read what it wrote."""
    from adapter.crypto import derive_key

    assert derive_key(TOKEN_URLSAFE_KEY, "PII_HASH_KEY") == derive_key(
        TOKEN_URLSAFE_KEY, "PII_HASH_KEY"
    )


def test_a_short_key_is_refused_and_the_value_never_appears() -> None:
    """32 characters is the floor. The message says so and says nothing about
    what was actually pasted."""
    from adapter.crypto import Sealer

    short = "x" * 31
    with pytest.raises(ValueError) as raised:
        Sealer(encryption_key=short, hash_key=TOKEN_URLSAFE_KEY)

    message = str(raised.value)
    assert "32" in message
    assert "PII_ENCRYPTION_KEY" in message
    assert short not in message


def test_hex_and_base64_are_not_sniffed() -> None:
    """A derivation is unambiguous; sniffing would make one string mean two
    different keys depending on which code path read it."""
    from adapter.crypto import derive_key

    hexish = "0a" * 32  # valid hex, 64 chars
    # Derived from the CHARACTERS, so it is not the decoded 32 bytes.
    assert derive_key(hexish, "PII_ENCRYPTION_KEY") != bytes.fromhex(hexish)
