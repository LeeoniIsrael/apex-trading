"""Kalshi request signing with no secret-bearing logging."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, ed25519


@dataclass(slots=True)
class KalshiSigner:
    key_id: str
    private_key_path: Path
    _private_key: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.key_id:
            raise ValueError("KALSHI_API_KEY_ID is required for authenticated requests")
        if not self.private_key_path.is_file():
            raise FileNotFoundError(f"Kalshi private key not found: {self.private_key_path}")
        self._private_key = serialization.load_pem_private_key(
            self.private_key_path.read_bytes(), password=None,
        )
        if not isinstance(self._private_key, (rsa.RSAPrivateKey, ed25519.Ed25519PrivateKey)):
            raise ValueError("unsupported Kalshi key type; use RSA or Ed25519")

    def headers(self, method: str, path_without_query: str) -> dict[str, str]:
        timestamp_ms = str(int(time.time() * 1000))
        message = f"{timestamp_ms}{method.upper()}{path_without_query}".encode()
        if isinstance(self._private_key, ed25519.Ed25519PrivateKey):
            signature = self._private_key.sign(message)
        else:
            signature = self._private_key.sign(
                message,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                            salt_length=padding.PSS.DIGEST_LENGTH),
                hashes.SHA256(),
            )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        }
