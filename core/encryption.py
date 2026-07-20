from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

LEGACY_MAGIC = b"BUHENC01"
MAGIC = b"BUHENC02"
SALT_SIZE = 16
NONCE_SIZE = 12
ITERATIONS = 390_000
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024


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

    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    with source_path.open("rb") as source, encrypted_path.open("wb") as destination:
        destination.write(MAGIC + salt + nonce)
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            destination.write(encryptor.update(chunk))
        destination.write(encryptor.finalize())
        destination.write(encryptor.tag)
    if remove_source:
        source_path.unlink()
    return EncryptionResult(source_path, encrypted_path, remove_source)


def decrypt_file(encrypted_path: Path, password: str, destination_path: Path) -> Path:
    with encrypted_path.open("rb") as source:
        magic = source.read(len(MAGIC))
        if magic == LEGACY_MAGIC:
            raw = source.read()
            salt = raw[:SALT_SIZE]
            nonce = raw[SALT_SIZE : SALT_SIZE + NONCE_SIZE]
            ciphertext = raw[SALT_SIZE + NONCE_SIZE :]
            plaintext = AESGCM(_derive_key(password, salt)).decrypt(nonce, ciphertext, None)
            destination_path.write_bytes(plaintext)
            return destination_path
        if magic != MAGIC:
            raise ValueError("Unsupported encrypted backup format")

        salt = source.read(SALT_SIZE)
        nonce = source.read(NONCE_SIZE)
        if len(salt) != SALT_SIZE or len(nonce) != NONCE_SIZE:
            raise ValueError("Encrypted backup header is incomplete")
        total_size = encrypted_path.stat().st_size
        header_size = len(MAGIC) + SALT_SIZE + NONCE_SIZE
        ciphertext_size = total_size - header_size - TAG_SIZE
        if ciphertext_size < 0:
            raise ValueError("Encrypted backup is incomplete")
        source.seek(total_size - TAG_SIZE)
        tag = source.read(TAG_SIZE)
        source.seek(header_size)
        cipher = Cipher(algorithms.AES(_derive_key(password, salt)), modes.GCM(nonce, tag))
        decryptor = cipher.decryptor()
        remaining = ciphertext_size
        with destination_path.open("wb") as destination:
            while remaining:
                chunk = source.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise ValueError("Encrypted backup is incomplete")
                destination.write(decryptor.update(chunk))
                remaining -= len(chunk)
            destination.write(decryptor.finalize())
    return destination_path
