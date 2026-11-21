import pytest
from datetime import datetime, timezone
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.security import create_access_token, hash_password
from app.db.models import (
    AuditLog,
    Base,
    Conversation,
    Issue,
    Repository,
    RepositoryMember,
    User,
)
from app.db.session import get_db
from app.main import create_app
from app.services.permissions import check_repo_access, has_permission


@pytest.fixture
def perm_db_session(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'perm_test.db'}",
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
def perm_client(perm_db_session, monkeypatch):
    import app.main as main_mod
    import app.db.session as sess_mod

    test_engine = perm_db_session.get_bind()
    monkeypatch.setattr(main_mod, "engine", test_engine)
    monkeypatch.setattr(sess_mod, "SessionLocal", sessionmaker(bind=test_engine))
    app = create_app()

    def override_get_db():
        try:
            yield perm_db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client


def test_role_permissions_matrix():
    admin = User(name="admin", role="owner")
    dev = User(name="dev", role="developer")
    viewer = User(name="viewer", role="viewer")

    assert has_permission(admin, "settings:manage") is True
    assert has_permission(admin, "repo:sync") is True
    assert has_permission(admin, "draft:approve") is True

    assert has_permission(dev, "agent:run") is True
    assert has_permission(dev, "draft:create") is True
    assert has_permission(dev, "draft:approve") is False
    assert has_permission(dev, "settings:manage") is False

    assert has_permission(viewer, "repo:read") is True
    assert has_permission(viewer, "agent:run") is False
    assert has_permission(viewer, "draft:create") is False


def test_repository_user_isolation_and_deletion_guard(perm_client, perm_db_session):
    # 创建用户 Alice (developer) 和 Bob (developer)
    user_alice = User(
        name="alice",
        email="alice@example.com",
        role="developer",
        hashed_password=hash_password("Pass123!"),
    )
    user_bob = User(
        name="bob",
        email="bob@example.com",
        role="developer",
        hashed_password=hash_password("Pass123!"),
    )
    user_admin = User(
        name="boss",
        email="boss@example.com",
        role="owner",
        hashed_password=hash_password("Pass123!"),
    )
    perm_db_session.add_all([user_alice, user_bob, user_admin])
    perm_db_session.commit()

    token_alice = create_access_token(data={"sub": str(user_alice.id), "role": "developer"})
    token_bob = create_access_token(data={"sub": str(user_bob.id), "role": "developer"})
    token_admin = create_access_token(data={"sub": str(user_admin.id), "role": "owner"})

    # 创建 Alice 的私有仓库
    repo_alice = Repository(
        owner="alice-org",
        name="alice-repo",
        full_name="alice-org/alice-repo",
        created_by=user_alice.id,
        is_public=False,
    )
    perm_db_session.add(repo_alice)
    perm_db_session.commit()

    # 1. Bob 尝试查看仓库列表，不应该看到 Alice 的私有仓库 (数据隔离)
    resp_bob = perm_client.get(
        "/api/repos",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_bob.status_code == 200
    repo_ids_bob = [item["id"] for item in resp_bob.json()]
    assert str(repo_alice.id) not in repo_ids_bob

    # 2. Alice 应该能看到自己的仓库
    resp_alice = perm_client.get(
        "/api/repos",
        headers={"Authorization": f"Bearer {token_alice}"},
    )
    assert resp_alice.status_code == 200
    repo_ids_alice = [item["id"] for item in resp_alice.json()]
    assert str(repo_alice.id) in repo_ids_alice

    # 3. Bob 尝试删除 Alice 的仓库，必须被拦截 (403 Forbidden 或 404 Not Found)
    del_resp_bob = perm_client.delete(
        f"/api/repos/{repo_alice.id}",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert del_resp_bob.status_code in (403, 404)
    assert perm_db_session.get(Repository, repo_alice.id) is not None

    # 4. 系统管理员 (admin) 拥有删除权限，且删除后记录审计日志
    del_resp_admin = perm_client.delete(
        f"/api/repos/{repo_alice.id}",
        headers={"Authorization": f"Bearer {token_admin}"},
    )
    assert del_resp_admin.status_code == 200
    assert perm_db_session.get(Repository, repo_alice.id) is None

    # 检查 AuditLog 是否记录该次删除
    audit = (
        perm_db_session.query(AuditLog)
        .filter(AuditLog.action == "repo.delete", AuditLog.target_id == str(repo_alice.id))
        .first()
    )
    assert audit is not None
    assert audit.user_id == user_admin.id


def test_repository_collaborator_management_lifecycle(perm_client, perm_db_session):
    user_alice = User(
        name="alice2",
        email="alice2@example.com",
        role="developer",
        hashed_password=hash_password("Pass123!"),
    )
    user_bob = User(
        name="bob2",
        email="bob2@example.com",
        role="developer",
        hashed_password=hash_password("Pass123!"),
    )
    perm_db_session.add_all([user_alice, user_bob])
    perm_db_session.commit()

    token_alice = create_access_token(data={"sub": str(user_alice.id), "role": "developer"})
    token_bob = create_access_token(data={"sub": str(user_bob.id), "role": "developer"})

    repo = Repository(
        owner="alice2-org",
        name="alice2-repo",
        full_name="alice2-org/alice2-repo",
        created_by=user_alice.id,
        is_public=False,
    )
    perm_db_session.add(repo)
    perm_db_session.commit()

    # 初始状态：Bob 无法访问 Alice 的私有仓库
    resp_init = perm_client.get(
        f"/api/repos/{repo.id}",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_init.status_code in (403, 404)

    # Alice 添加 Bob 为协作者 (developer)
    add_resp = perm_client.post(
        f"/api/repos/{repo.id}/members",
        headers={"Authorization": f"Bearer {token_alice}"},
        json={"user_id": str(user_bob.id), "role": "developer"},
    )
    assert add_resp.status_code == 200
    data = add_resp.json()
    assert data["role"] == "developer"
    assert data["user_name"] == "bob2"

    # Alice 获取成员列表
    list_resp = perm_client.get(
        f"/api/repos/{repo.id}/members",
        headers={"Authorization": f"Bearer {token_alice}"},
    )
    assert list_resp.status_code == 200
    members = list_resp.json()
    assert any(m["user_id"] == str(user_bob.id) for m in members)

    # Bob 现在可以访问该仓库
    resp_after_add = perm_client.get(
        f"/api/repos/{repo.id}",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_after_add.status_code == 200
    assert resp_after_add.json()["name"] == "alice2-repo"

    # Alice 移除 Bob 的协作者身份
    del_member_resp = perm_client.delete(
        f"/api/repos/{repo.id}/members/{user_bob.id}",
        headers={"Authorization": f"Bearer {token_alice}"},
    )
    assert del_member_resp.status_code == 200

    # Bob 再次访问该仓库被拦截 (403 或 404)
    resp_after_del = perm_client.get(
        f"/api/repos/{repo.id}",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_after_del.status_code in (403, 404)


def test_repository_issues_and_conversations_isolation(perm_client, perm_db_session):
    user_alice = User(
        name="alice3",
        email="alice3@example.com",
        role="developer",
        hashed_password=hash_password("Pass123!"),
    )
    user_bob = User(
        name="bob3",
        email="bob3@example.com",
        role="developer",
        hashed_password=hash_password("Pass123!"),
    )
    perm_db_session.add_all([user_alice, user_bob])
    perm_db_session.commit()

    token_alice = create_access_token(data={"sub": str(user_alice.id), "role": "developer"})
    token_bob = create_access_token(data={"sub": str(user_bob.id), "role": "developer"})

    repo = Repository(
        owner="alice3-org",
        name="alice3-repo",
        full_name="alice3-org/alice3-repo",
        created_by=user_alice.id,
        is_public=False,
    )
    perm_db_session.add(repo)
    perm_db_session.commit()

    issue = Issue(
        repo_id=repo.id,
        github_issue_id=999,
        number=1,
        title="Confidential security vulnerability",
        body="Secret details",
        state="open",
        labels=["security"],
        author="alice3",
        created_at=datetime.now(timezone.utc),
    )
    perm_db_session.add(issue)
    perm_db_session.commit()

    # 1. Bob 尝试查看 Alice 仓库的 Issue 列表，必须被拦截 (403 或 404)
    issues_resp_bob = perm_client.get(
        f"/api/repos/{repo.id}/issues",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert issues_resp_bob.status_code in (403, 404)

    # 2. Alice 能够查看自己的 Issue 列表
    issues_resp_alice = perm_client.get(
        f"/api/repos/{repo.id}/issues",
        headers={"Authorization": f"Bearer {token_alice}"},
    )
    assert issues_resp_alice.status_code == 200
    assert len(issues_resp_alice.json()) == 1

    # 3. Bob 尝试查看单个 Issue 详情，被拦截
    issue_resp_bob = perm_client.get(
        f"/api/repos/{repo.id}/issues/{issue.id}",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert issue_resp_bob.status_code in (403, 404)

    # 4. Bob 尝试在 Alice 仓库中创建对话，被拦截
    conv_resp_bob = perm_client.post(
        "/api/conversations",
        headers={"Authorization": f"Bearer {token_bob}"},
        json={"repo_id": str(repo.id), "title": "Bob eavesdropping"},
    )
    assert conv_resp_bob.status_code in (403, 404)


def test_core_routes_require_authentication_when_auth_required(
    perm_client, perm_db_session, monkeypatch
):
    from app.core.config import settings
    from app.db.models import WorkflowRun, Document

    monkeypatch.setattr(settings, "auth_required", True)

    repo = Repository(
        owner="isolated-org",
        name="isolated-repo",
        full_name="isolated-org/isolated-repo",
        is_public=False,
    )
    perm_db_session.add(repo)
    perm_db_session.commit()

    run = WorkflowRun(
        repo_id=repo.id,
        github_run_id=5001,
        name="private-ci",
        status="completed",
        conclusion="failure",
        created_at=datetime.now(timezone.utc),
    )
    doc = Document(
        repo_id=repo.id,
        source_type="project_doc",
        source_id=uuid4(),
        title="Secret Spec",
        content="Sensitive internals",
        metadata={"title": "Secret Spec"},
    )
    perm_db_session.add_all([run, doc])
    perm_db_session.commit()

    # 未携带 Authorization Header 请求核心路由，必须统一返回 401 Unauthorized
    endpoints = [
        f"/api/repos/{repo.id}/workflow-runs",
        f"/api/workflow-runs/{run.id}",
        f"/api/workflow-runs/{run.id}/failed-jobs",
        f"/api/repos/{repo.id}/pull-requests",
        f"/api/repos/{repo.id}/code-symbols",
        f"/api/repos/{repo.id}/knowledge",
        f"/api/rag/repositories/{repo.id}",
        f"/api/chat/history?repo_id={repo.id}",
    ]
    for endpoint in endpoints:
        resp = perm_client.get(endpoint)
        assert resp.status_code == 401, f"{endpoint} expected 401, got {resp.status_code}"


def test_cross_user_ci_and_rag_isolation(perm_client, perm_db_session):
    from app.db.models import WorkflowRun, Document

    user_alice = User(
        name="alice_ci",
        email="alice_ci@example.com",
        role="developer",
        hashed_password=hash_password("Pass123!"),
    )
    user_bob = User(
        name="bob_ci",
        email="bob_ci@example.com",
        role="developer",
        hashed_password=hash_password("Pass123!"),
    )
    perm_db_session.add_all([user_alice, user_bob])
    perm_db_session.commit()

    token_alice = create_access_token(data={"sub": str(user_alice.id), "role": "developer"})
    token_bob = create_access_token(data={"sub": str(user_bob.id), "role": "developer"})

    repo_alice = Repository(
        owner="alice-ci-org",
        name="alice-ci-repo",
        full_name="alice-ci-org/alice-ci-repo",
        created_by=user_alice.id,
        is_public=False,
    )
    perm_db_session.add(repo_alice)
    perm_db_session.commit()

    run = WorkflowRun(
        repo_id=repo_alice.id,
        github_run_id=6001,
        name="backend-test",
        status="completed",
        conclusion="failure",
        created_at=datetime.now(timezone.utc),
    )
    doc = Document(
        repo_id=repo_alice.id,
        source_type="project_doc",
        source_id=uuid4(),
        title="Alice Doc",
        content="Confidential content",
        metadata={"title": "Alice Doc"},
    )
    perm_db_session.add_all([run, doc])
    perm_db_session.commit()

    # 1. Bob 尝试查看 Alice 仓库的 CI 运行记录，必须被拦截 (403 或 404)
    resp_bob_ci = perm_client.get(
        f"/api/repos/{repo_alice.id}/workflow-runs",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_bob_ci.status_code in (403, 404)

    # 2. Bob 尝试查看单个私有 CI run 详情，被拦截
    resp_bob_run = perm_client.get(
        f"/api/workflow-runs/{run.id}",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_bob_run.status_code in (403, 404)

    # 3. Bob 尝试查看 Alice 仓库的 RAG 详情，被拦截
    resp_bob_rag = perm_client.get(
        f"/api/rag/repositories/{repo_alice.id}",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_bob_rag.status_code in (403, 404)

    # 4. Bob 尝试查看 Alice 仓库的 Knowledge 文档，被拦截
    resp_bob_know = perm_client.get(
        f"/api/repos/{repo_alice.id}/knowledge",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_bob_know.status_code in (403, 404)

    # 5. Alice 能够正常访问自己的资源
    resp_alice_ci = perm_client.get(
        f"/api/repos/{repo_alice.id}/workflow-runs",
        headers={"Authorization": f"Bearer {token_alice}"},
    )
    assert resp_alice_ci.status_code == 200
    assert len(resp_alice_ci.json()) == 1
