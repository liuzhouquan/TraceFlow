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
def auth_client(auth_db_session, monkeypatch):
    import app.main as main_mod
    import app.db.session as sess_mod

    test_engine = auth_db_session.get_bind()
    monkeypatch.setattr(main_mod, "engine", test_engine)
    monkeypatch.setattr(sess_mod, "SessionLocal", sessionmaker(bind=test_engine))
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


def test_public_registration_cannot_choose_privileged_role(auth_client, auth_db_session):
    # 尝试公开注册管理员角色 owner 必须被拒绝 (HTTP 400)
    resp_owner = auth_client.post(
        "/api/auth/register",
        json={
            "name": "hacker1",
            "email": "hacker1@example.com",
            "password": "Password123!",
            "role": "owner",
        },
    )
    assert resp_owner.status_code == 400
    assert "不允许指定特权角色" in resp_owner.json()["detail"]

    # 尝试公开注册管理员角色 maintainer 必须被拒绝 (HTTP 400)
    resp_maintainer = auth_client.post(
        "/api/auth/register",
        json={
            "name": "hacker2",
            "email": "hacker2@example.com",
            "password": "Password123!",
            "role": "maintainer",
        },
    )
    assert resp_maintainer.status_code == 400
    assert "不允许指定特权角色" in resp_maintainer.json()["detail"]

    # 不指定角色注册，默认应为普通角色 developer
    resp_default = auth_client.post(
        "/api/auth/register",
        json={
            "name": "normal_user",
            "email": "normal@example.com",
            "password": "Password123!",
        },
    )
    assert resp_default.status_code == 201
    assert resp_default.json()["user"]["role"] == "developer"


def test_production_mode_enforces_auth_and_rejects_insecure_key(auth_client, monkeypatch):
    # 1. 模拟生产环境 APP_ENV=production
    monkeypatch.setattr(settings, "app_env", "production")

    # 在生产模式下，未提供 Bearer 令牌调用 /api/auth/me 必须返回 401，绝不可退化至 demo 用户
    resp = auth_client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "需要身份认证"

    # 2. 生产环境下使用默认不安全的 JWT Secret Key 必须抛出异常
    monkeypatch.setattr(settings, "jwt_secret_key", "traceflow-jwt-secret-key-change-in-production")
    with pytest.raises(ValueError, match="检测到不安全的 JWT_SECRET_KEY"):
        settings.validate_security_settings()

    # 配置足够强度的密钥后校验通过
    monkeypatch.setattr(settings, "jwt_secret_key", "a" * 32)
    settings.validate_security_settings()
