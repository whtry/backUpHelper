from __future__ import annotations

from pathlib import Path

from core.encryption import decrypt_file, encrypt_file


def test_encrypt_and_decrypt_file(tmp_path: Path) -> None:
    source = tmp_path / "backup.iso"
    source.write_bytes(b"backup data")

    encrypted = encrypt_file(source, "secret")
    restored = decrypt_file(encrypted.encrypted_path, "secret", tmp_path / "backup.iso.restored")

    assert not source.exists()
    assert encrypted.encrypted_path.suffix == ".enc"
    assert restored.read_bytes() == b"backup data"


def test_encrypt_and_decrypt_large_file_in_chunks(tmp_path: Path) -> None:
    source = tmp_path / "large-backup.zip"
    source.write_bytes((b"0123456789abcdef" * 131_072) + b"tail")

    encrypted = encrypt_file(source, "secret")
    restored = decrypt_file(encrypted.encrypted_path, "secret", tmp_path / "restored.zip")

    assert restored.read_bytes().endswith(b"tail")
    assert restored.stat().st_size == 2 * 1024 * 1024 + 4
