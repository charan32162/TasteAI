import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

ALGORITHM = "HS256"
ACCESS_TOKEN_HOURS = 12
_development_secret = secrets.token_urlsafe(48)


def validate_security_config() -> None:
    _secret()


def _secret() -> str:
    value = os.getenv("TASTEAI_SECRET_KEY")
    if not value:
        if os.getenv("TASTEAI_ENV", "development").lower() == "production":
            raise RuntimeError("TASTEAI_SECRET_KEY must be configured in production")
        return _development_secret
    if len(value) < 32 and os.getenv("TASTEAI_ENV", "development").lower() == "production":
        raise RuntimeError("TASTEAI_SECRET_KEY must be at least 32 characters in production")
    return value


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(user_id), "iat": now, "exp": now + timedelta(hours=ACCESS_TOKEN_HOURS)},
        _secret(),
        algorithm=ALGORITHM,
    )


def decode_access_token(token: str) -> int | None:
    try:
        subject = jwt.decode(token, _secret(), algorithms=[ALGORITHM]).get("sub")
        return int(subject)
    except (jwt.InvalidTokenError, TypeError, ValueError):
        return None
