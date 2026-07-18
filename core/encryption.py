from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"BUHENC01"
SALT_SIZE = 16
NONCE_SIZE = 12
ITERATIONS = 390_000


@dataclass(frozen=True)
class EncryptionResult:
    source_path: Path
    encrypted_path: Path
    removed_source: bool


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_file(source_path: Path, password: str, remove_source: bool = True) -> EncryptionResult:
    if not password:
        raise ValueError("Encryption password cannot be empty")

    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    key = _derive_key(password, salt)
    encrypted_path = source_path.with_suffix(source_path.suffix + ".enc")

    data = source_path.read_bytes()
    ciphertext = AESGCM(key).encrypt(nonce, data, None)
    encrypted_path.write_bytes(MAGIC + salt + nonce + ciphertext)
    if remove_source:
        source_path.unlink()
    return EncryptionResult(source_path, encrypted_path, remove_source)


def decrypt_file(encrypted_path: Path, password: str, destination_path: Path) -> Path:
    raw = encrypted_path.read_bytes()
    if not raw.startswith(MAGIC):
        raise ValueError("Unsupported encrypted backup format")
    offset = len(MAGIC)
    salt = raw[offset : offset + SALT_SIZE]
    offset += SALT_SIZE
    nonce = raw[offset : offset + NONCE_SIZE]
    offset += NONCE_SIZE
    ciphertext = raw[offset:]
    key = _derive_key(password, salt)
    plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
    destination_path.write_bytes(plaintext)
    return destination_path
