import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _derive_demo_key(secret: str) -> bytes:
    # Fernet 需要 urlsafe base64 编码的 32 字节密钥。
    # 这里的确定性本地开发派生方式适合课程项目，不适合生产环境。
    import base64

    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())


def encrypt_token(token: str, secret: str) -> str:
    return Fernet(_derive_demo_key(secret)).encrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted: str, secret: str) -> str | None:
    try:
        return Fernet(_derive_demo_key(secret)).decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None


def hash_password(password: str) -> str:
    """使用 PBKDF2-HMAC-SHA256 对密码进行加盐哈希 (100,000 次迭代)"""
    salt = secrets.token_bytes(16)
    iterations = 100_000
    hash_bytes = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${hash_bytes.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """恒定时间比对密码，防范时序侧信道攻击"""
    try:
        parts = hashed_password.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected_hash = bytes.fromhex(parts[3])
        actual_hash = hashlib.pbkdf2_hmac(
            "sha256", plain_password.encode("utf-8"), salt, iterations
        )
        return hmac.compare_digest(actual_hash, expected_hash)
    except Exception:
        return False


def create_access_token(
    data: dict,
    expires_minutes: int | None = None,
) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire_delta = timedelta(
        minutes=expires_minutes
        if expires_minutes is not None
        else settings.jwt_access_token_expire_minutes
    )
    to_encode.update({"iat": now, "exp": now + expire_delta})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except (jwt.PyJWTError, Exception):
        return None
