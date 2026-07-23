from pathlib import Path

from mato_loop_backend.security import CredentialCipher, hash_password, verify_password


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("a sufficiently long password")

    assert verify_password("a sufficiently long password", hashed)
    assert not verify_password("wrong password", hashed)


def test_credential_cipher_persists_key(tmp_path: Path) -> None:
    key_path = tmp_path / "secrets" / "credential.key"
    first = CredentialCipher(key_path)
    encrypted = first.encrypt({"api_key": "secret"})

    second = CredentialCipher(key_path)

    assert second.decrypt(encrypted) == {"api_key": "secret"}
    assert key_path.stat().st_mode & 0o777 == 0o600
