import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.db.models import Base, User
from app.db.session import get_db
from app.main import create_app


@pytest.fixture
def auth_db_session(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'auth_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def auth_client(auth_db_session):
    app = create_app()

    def override_get_db():
        try:
            yield auth_db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client


def test_password_hashing():
    raw_password = "super-secret-password-123"
    hashed = hash_password(raw_password)
    assert hashed != raw_password
    assert hashed.startswith("pbkdf2_sha256$")
    assert verify_password(raw_password, hashed) is True
    assert verify_password("wrong-password", hashed) is False
    assert verify_password(raw_password, "corrupted_hash") is False


def test_jwt_token_lifecycle():
    user_id = "00000000-0000-0000-0000-000000000001"
    token = create_access_token(
        data={"sub": user_id, "role": "developer", "name": "alice"},
        expires_minutes=60,
    )
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == user_id
    assert payload["role"] == "developer"
    assert payload["name"] == "alice"

    # Invalid token check
    assert decode_access_token("invalid.token.signature") is None


def test_auth_register_and_login_flow(auth_client, auth_db_session):
    # 1. 正常注册
    register_payload = {
        "name": "alice",
        "email": "Alice@Example.com",
        "password": "Password123!",
        "role": "developer",
    }
    resp = auth_client.post("/api/auth/register", json=register_payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "alice@example.com"  # 规范化小写
    assert data["user"]["name"] == "alice"
    assert data["user"]["role"] == "developer"

    # 2. 重复邮箱注册失败 (Fail-Closed)
    dup_resp = auth_client.post("/api/auth/register", json=register_payload)
    assert dup_resp.status_code == 400
    assert "已存在" in dup_resp.json()["detail"]

    # 3. 正常登录
    login_resp = auth_client.post(
        "/api/auth/login",
        json={"username": "alice@example.com", "password": "Password123!"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    assert token

    # 4. 密码错误登录失败
    bad_login = auth_client.post(
        "/api/auth/login",
        json={"username": "alice@example.com", "password": "WrongPassword"},
    )
    assert bad_login.status_code == 401
    assert "用户名或密码错误" in bad_login.json()["detail"]

    # 5. /api/auth/me 获取当前登录用户信息
    me_resp = auth_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "alice@example.com"

    # 6. 非法 Token 请求 /api/auth/me 必须失败
    bad_me = auth_client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer bad-token"},
    )
    assert bad_me.status_code == 401


def test_inactive_user_cannot_login_or_access(auth_client, auth_db_session):
    hashed = hash_password("Password123!")
    user = User(
        name="bob",
        email="bob@example.com",
        hashed_password=hashed,
        role="viewer",
        is_active=False,
    )
    auth_db_session.add(user)
    auth_db_session.commit()

    # 登录被拒
    login_resp = auth_client.post(
        "/api/auth/login",
        json={"username": "bob@example.com", "password": "Password123!"},
    )
    assert login_resp.status_code == 401
    assert "账号已被停用" in login_resp.json()["detail"]

    # 携带有效 token 访问也必须被拒
    token = create_access_token(data={"sub": str(user.id), "role": "viewer"})
    me_resp = auth_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 401
