import os
import base64

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.core.config import settings


def _get_key() -> bytes:
    return base64.b64decode(settings.master_encryption_key)


def encrypt(plaintext: str) -> tuple[bytes, bytes, bytes]:
    key = _get_key()
    iv = os.urandom(12)
    aesgcm = AESGCM(key)
    result = aesgcm.encrypt(iv, plaintext.encode(), None)
    ciphertext, tag = result[:-16], result[-16:]
    return ciphertext, iv, tag


def decrypt(ciphertext: bytes, iv: bytes, tag: bytes) -> str:
    key = _get_key()
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ciphertext + tag, None)
    return plaintext.decode()
