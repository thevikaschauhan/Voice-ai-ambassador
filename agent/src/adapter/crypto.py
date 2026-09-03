"""The authenticated-encryption boundary for buyer-derived payloads.

docs/10-: full transcripts, summaries, contacts and admin notes live only in
Postgres and authenticated API responses, and they are encrypted with
authenticated application-layer encryption BEFORE they reach Postgres. The
envelope records a key version and binds lead id plus field name as associated
data.

Two keys, two jobs, and the difference is stated rather than assumed:

  PII_ENCRYPTION_KEY  AES-256-GCM. Reversible, because a human has to be able
                      to read a transcript and phone a buyer back.
  PII_HASH_KEY        keyed HMAC-SHA-256, for equality and duplicate detection
                      without indexing the clear value. **Hashing is not
                      encryption and is not presented as one** - a fingerprint
                      is a one-way label, and nothing in the product should
                      ever try to read a buyer's number out of it.

AAD IS THE POINT OF USING AEAD HERE. Encrypting alone would leave a ciphertext
that decrypts wherever it is put; binding `lead_id` and the field path means a
row's brief cannot be moved into another row, or into the summary column, and
still open. A tampered or relocated envelope fails loudly instead of returning
somebody else's words.

`cryptography` is a dependency rather than a hand-rolled construction because
authenticated encryption is the canonical thing not to write yourself.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ALGORITHM = "aes-256-gcm"
# v1 IS the HKDF derivation described in `derive_key`. Changing how a key is
# derived changes what every stored envelope means, so it changes this too -
# nothing is persisted yet, so v1 is still free to mean this.
KEY_VERSION = "v1"
# 96 bits, the AES-GCM standard nonce size: the only size the construction is
# specified for, and the size that lets the counter block be used as intended.
NONCE_BYTES = 12
_KEY_BYTES = 32


class EnvelopeError(RuntimeError):
    """The envelope did not open: wrong key, wrong lead, wrong field, or
    tampered ciphertext. Deliberately does not say which."""


# Any string of at least this many CHARACTERS. Not a byte length and not a
# format: the human generates these with `openssl rand -base64 32` or
# `secrets.token_urlsafe(32)`, and both produce 43 characters that are neither
# hex nor 32 bytes of utf-8. The first version of this parsed instead of
# derived, accepted 64-hex or exactly-32-byte strings, and would have put a
# worker on Railway that refused every job over a config error. A key format
# is not something anyone should have to infer from a variable name.
MIN_KEY_CHARACTERS = 32


def derive_key(value: str, name: str) -> bytes:
    """A 32-byte key from any sufficiently long string, via HKDF-SHA256.

    DERIVED, never parsed, and deliberately without sniffing for hex or
    base64: a derivation is unambiguous, so one string means one key on every
    code path, while sniffing would make the same string mean two different
    keys depending on who read it.

    `info` is the VARIABLE NAME, which is what stops the encryption key and
    the fingerprint key colliding when someone pastes one generated value into
    both variables - the realistic mistake, not a theoretical one.

    Stable for a given string, because a process that derived a different key
    on restart could not read what it wrote.
    """
    if not value:
        raise ValueError(
            f"{name} is not set. Buyer-derived payloads are encrypted before "
            "they reach Postgres (docs/10-), so there is no configuration in "
            "which this may be skipped."
        )
    if len(value) < MIN_KEY_CHARACTERS:
        # The length only, never the value.
        raise ValueError(
            f"{name} must be at least {MIN_KEY_CHARACTERS} characters; got "
            f"{len(value)}. Generate one with `openssl rand -base64 32`."
        )
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_BYTES,
        # No salt: there is one input secret per variable and nothing to
        # coordinate a salt with across two services. `info` carries the
        # separation instead, which is what HKDF's info field is for.
        salt=None,
        info=name.encode("utf-8"),
    ).derive(value.encode("utf-8"))


class Sealer:
    """Seals and opens field envelopes. Holds keys and nothing else.

    Constructed eagerly and refuses a missing or wrong-length key, so a
    misconfigured process fails at startup rather than at the end of the first
    call - the point at which the alternative is writing readable buyer text.
    """

    def __init__(self, *, encryption_key: str, hash_key: str) -> None:
        self._aead = AESGCM(derive_key(encryption_key, "PII_ENCRYPTION_KEY"))
        self._hash_key = derive_key(hash_key, "PII_HASH_KEY")

    @classmethod
    def from_env(cls) -> "Sealer":
        return cls(
            encryption_key=os.environ.get("PII_ENCRYPTION_KEY", ""),
            hash_key=os.environ.get("PII_HASH_KEY", ""),
        )

    @staticmethod
    def _associated(lead_id: Any, field_path: str) -> bytes:
        return f"{lead_id}|{field_path}".encode("utf-8")

    def seal(self, lead_id: Any, field_path: str, plaintext: bytes) -> dict[str, Any]:
        nonce = os.urandom(NONCE_BYTES)
        return {
            "algorithm": ALGORITHM,
            "key_version": KEY_VERSION,
            "nonce": nonce,
            "ciphertext": self._aead.encrypt(
                nonce, plaintext, self._associated(lead_id, field_path)
            ),
        }

    def open(self, lead_id: Any, field_path: str, envelope: dict[str, Any]) -> bytes:
        if envelope["algorithm"] != ALGORITHM:
            # The algorithm is a fixed implementation enum, never caller
            # supplied (docs/02-): an envelope naming another one is not
            # something to try, it is something wrong.
            raise EnvelopeError("unexpected envelope algorithm")
        try:
            return self._aead.decrypt(
                envelope["nonce"],
                envelope["ciphertext"],
                self._associated(lead_id, field_path),
            )
        except InvalidTag as exc:
            raise EnvelopeError(
                "the envelope did not open for this lead and field"
            ) from exc

    def fingerprint(self, canonical_value: str) -> str:
        """A keyed one-way label for equality, never a way back to the value."""
        return hmac.new(
            self._hash_key, canonical_value.encode("utf-8"), hashlib.sha256
        ).hexdigest()
