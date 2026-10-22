"""JWT: выдача, проверка подписи и срока, отказ от alg=none."""

import base64
import json
from datetime import timedelta

import jwt
import pytest

from corag_contracts.auth import InvalidToken, create_access_token, decode_token

SECRET = "test-secret-0123456789abcdef0123456789"


def test_round_trip():
    token = create_access_token(42, "admin", SECRET, timedelta(minutes=30))
    claims = decode_token(token, SECRET)
    assert (claims.user_id, claims.role) == (42, "admin")
    assert claims.expires_at - claims.issued_at == timedelta(minutes=30)


def test_expired():
    token = create_access_token(1, "researcher", SECRET, timedelta(seconds=-1))
    with pytest.raises(InvalidToken, match="expired"):
        decode_token(token, SECRET)


def test_wrong_secret():
    token = create_access_token(1, "researcher", "another-secret-0123456789abcdef01", timedelta(1))
    with pytest.raises(InvalidToken, match="Signature"):
        decode_token(token, SECRET)


def test_tampered_payload():
    """Подмена role в payload без переподписи ломает подпись."""
    header, payload, signature = create_access_token(
        1, "researcher", SECRET, timedelta(minutes=5)
    ).split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=="))
    data["role"] = "admin"
    forged = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    with pytest.raises(InvalidToken):
        decode_token(f"{header}.{forged}.{signature}", SECRET)


def test_alg_none_rejected():
    token = jwt.encode(
        {"sub": "1", "role": "admin", "iat": 0, "exp": 2**31}, key=None, algorithm="none"
    )
    with pytest.raises(InvalidToken):
        decode_token(token, SECRET)


def test_missing_claims():
    token = jwt.encode({"sub": "1"}, SECRET, algorithm="HS256")
    with pytest.raises(InvalidToken, match="role|iat|exp"):
        decode_token(token, SECRET)
