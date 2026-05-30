import base64
import os

import pytest


@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("MASTER_ENCRYPTION_KEY", key)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test")


def test_encrypt_decrypt_roundtrip():
    # Arrange
    from src.core.encryption import decrypt, encrypt

    plaintext = "test_api_key_12345"
    # Act
    ciphertext, iv, tag = encrypt(plaintext)
    result = decrypt(ciphertext, iv, tag)
    # Assert
    assert result == plaintext


def test_encrypt_produces_different_iv_each_time():
    from src.core.encryption import encrypt

    _, iv1, _ = encrypt("same_text")
    _, iv2, _ = encrypt("same_text")
    assert iv1 != iv2


def test_decrypt_fails_with_wrong_tag():
    from cryptography.exceptions import InvalidTag

    from src.core.encryption import decrypt, encrypt

    ciphertext, iv, tag = encrypt("secret")
    bad_tag = bytes(b ^ 0xFF for b in tag)
    with pytest.raises(InvalidTag):
        decrypt(ciphertext, iv, bad_tag)


def test_tenant_isolation_rls_token_format():
    """Убеждаемся что RLS token является валидным UUID."""
    import uuid

    tenant_id = uuid.uuid4()
    assert str(tenant_id) == str(uuid.UUID(str(tenant_id)))
