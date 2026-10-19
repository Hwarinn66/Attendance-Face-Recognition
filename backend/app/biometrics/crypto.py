"""Application-level encryption of face templates (AES-256-GCM).

* Keys come from ``BIOMETRIC_ENCRYPTION_KEYS`` (``kid:base64key,...``); the first key encrypts, all keys
  decrypt — enabling rotation via ``python -m app.cli rotate-template-keys``.
* Associated data binds each ciphertext to its template id, owner and model, so ciphertext moved to
  another row (or another user) fails authentication instead of silently matching.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

import numpy as np
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.biometrics.types import Embedding


class TemplateDecryptionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SealedTemplate:
    key_id: str
    nonce: bytes
    ciphertext: bytes


def _aad(template_id: uuid.UUID, user_id: uuid.UUID, model_name: str, dim: int) -> bytes:
    return f"absen-template/v1|{template_id}|{user_id}|{model_name}|{dim}".encode()


class TemplateCipher:
    def __init__(self, keyring: dict[str, bytes]) -> None:
        if not keyring:
            raise ValueError("empty keyring")
        self._keys = dict(keyring)
        self.active_key_id = next(iter(keyring))

    def seal(
        self, embedding: Embedding, *, template_id: uuid.UUID, user_id: uuid.UUID, model_name: str
    ) -> SealedTemplate:
        vec = np.asarray(embedding, dtype="<f4").reshape(-1)
        nonce = os.urandom(12)
        ct = AESGCM(self._keys[self.active_key_id]).encrypt(
            nonce, vec.tobytes(), _aad(template_id, user_id, model_name, vec.size)
        )
        return SealedTemplate(self.active_key_id, nonce, ct)

    def open(
        self,
        sealed: SealedTemplate,
        *,
        template_id: uuid.UUID,
        user_id: uuid.UUID,
        model_name: str,
        dim: int,
    ) -> Embedding:
        key = self._keys.get(sealed.key_id)
        if key is None:
            raise TemplateDecryptionError(f"unknown key id '{sealed.key_id}'")
        try:
            pt = AESGCM(key).decrypt(sealed.nonce, sealed.ciphertext, _aad(template_id, user_id, model_name, dim))
        except InvalidTag as exc:
            raise TemplateDecryptionError("template authentication failed") from exc
        vec = np.frombuffer(pt, dtype="<f4").astype(np.float32)
        if vec.size != dim:
            raise TemplateDecryptionError("template dimension mismatch")
        return vec
