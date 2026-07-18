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
