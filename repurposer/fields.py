import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models


class EncryptedTextField(models.TextField):
    """TextField that encrypts values at rest while reading legacy text."""

    prefix = "enc:v1:"

    @staticmethod
    def _fernet():
        secret = settings.TOKEN_ENCRYPTION_KEY
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(digest))

    def from_db_value(self, value, expression, connection):
        return self.to_python(value)

    def to_python(self, value):
        if value is None or not isinstance(value, str) or not value.startswith(self.prefix):
            return value
        try:
            encrypted = value[len(self.prefix):].encode("ascii")
            return self._fernet().decrypt(encrypted).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
            raise ValueError(
                "Could not decrypt a stored platform token. "
                "Check that TOKEN_ENCRYPTION_KEY has not changed."
            ) from exc

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value.startswith(self.prefix):
            return value
        encrypted = self._fernet().encrypt(value.encode("utf-8")).decode("ascii")
        return f"{self.prefix}{encrypted}"
