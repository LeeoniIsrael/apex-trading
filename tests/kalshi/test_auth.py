import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa

from src.kalshi.auth import KalshiSigner


@pytest.mark.parametrize('kind', ['rsa_pkcs1', 'rsa_pkcs8', 'ed25519'])
def test_request_signature_verifies_with_registered_public_key(tmp_path, monkeypatch, kind):
    key = (ed25519.Ed25519PrivateKey.generate() if kind == 'ed25519'
           else rsa.generate_private_key(public_exponent=65537, key_size=2048))
    encoding = (serialization.PrivateFormat.TraditionalOpenSSL if kind == 'rsa_pkcs1'
                else serialization.PrivateFormat.PKCS8)
    path = tmp_path/'test.pem'
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, encoding, serialization.NoEncryption()))
    monkeypatch.setattr('src.kalshi.auth.time.time', lambda: 1700000000)
    signer = KalshiSigner('test-key', path)
    headers = signer.headers('get', '/trade-api/v2/portfolio/balance')
    message = b'1700000000000GET/trade-api/v2/portfolio/balance'
    signature = base64.b64decode(headers['KALSHI-ACCESS-SIGNATURE'])
    if kind == 'ed25519':
        key.public_key().verify(signature, message)
    else:
        key.public_key().verify(signature, message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256())
    assert headers['KALSHI-ACCESS-KEY'] == 'test-key'
    assert headers['KALSHI-ACCESS-TIMESTAMP'] == '1700000000000'


def test_unsupported_key_is_rejected_at_load(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    path = tmp_path/'unsupported.pem'
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with pytest.raises(ValueError, match='unsupported Kalshi key type'):
        KalshiSigner('test-key', path)
