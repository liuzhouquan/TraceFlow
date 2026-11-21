import pytest
from datetime import datetime, timezone
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
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
    import app.api.routes.evaluations as eval_mod

    test_engine = perm_db_session.get_bind()
    monkeypatch.setattr(main_mod, "engine", test_engine)
    monkeypatch.setattr(sess_mod, "SessionLocal", sessionmaker(bind=test_engine))
    monkeypatch.setattr(eval_mod, "SessionLocal", sessionmaker(bind=test_engine))
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


def test_anonymous_access_blocked_on_all_unprotected_routes(
    perm_client, perm_db_session, monkeypatch
):
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "demo_mode", False)

    fake_repo_id = uuid4()

    # 1. POST /api/repos/{repo_id}/memory-index/snooze
    res = perm_client.post(f"/api/repos/{fake_repo_id}/memory-index/snooze")
    assert res.status_code == 401

    # 2. GET /api/chat/feedback/metrics
    res = perm_client.get(f"/api/chat/feedback/metrics?repo_id={fake_repo_id}")
    assert res.status_code == 401

    # 3. POST /api/search
    res = perm_client.post("/api/search", json={"repo_id": str(fake_repo_id), "query": "auth test"})
    assert res.status_code == 401

    # 4. POST /api/reports/weekly
    res = perm_client.post(
        "/api/reports/weekly",
        json={"repo_id": str(fake_repo_id), "start_date": "2026-01-01", "end_date": "2026-01-07"},
    )
    assert res.status_code == 401

    # 5. POST /api/reports/repo-health
    res = perm_client.post(
        "/api/reports/repo-health",
        json={"repo_id": str(fake_repo_id), "start_date": "2026-01-01", "end_date": "2026-01-07"},
    )
    assert res.status_code == 401

    # 6. GET /api/skills
    res = perm_client.get("/api/skills")
    assert res.status_code == 401

    # 7. GET /api/action-drafts
    res = perm_client.get("/api/action-drafts")
    assert res.status_code == 401

    # 8. POST /api/workspaces/multi-repo-report
    res = perm_client.post(
        "/api/workspaces/multi-repo-report",
        json={
            "repo_ids": [str(fake_repo_id)],
            "start_date": "2026-01-01",
            "end_date": "2026-01-07",
        },
    )
    assert res.status_code == 401


def test_unrelated_developer_and_viewer_cross_repo_isolation(
    perm_client, perm_db_session, monkeypatch
):
    from app.db.models import ActionDraft, RepositoryMember

    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "demo_mode", False)

    # 准备三个用户：Alice（Developer，Repo属主），Bob（Developer，无关开发者），Charlie（Viewer，只读协作者）
    user_alice = User(
        name="alice-dev",
        role="developer",
        email="alice.draft@test.com",
        hashed_password=hash_password("pw123"),
        is_active=True,
    )
    user_bob = User(
        name="bob-dev",
        role="developer",
        email="bob.draft@test.com",
        hashed_password=hash_password("pw123"),
        is_active=True,
    )
    user_charlie = User(
        name="charlie-view",
        role="viewer",
        email="charlie.draft@test.com",
        hashed_password=hash_password("pw123"),
        is_active=True,
    )
    perm_db_session.add_all([user_alice, user_bob, user_charlie])
    perm_db_session.commit()

    token_alice = create_access_token(data={"sub": str(user_alice.id), "role": "developer"})
    token_bob = create_access_token(data={"sub": str(user_bob.id), "role": "developer"})
    token_charlie = create_access_token(data={"sub": str(user_charlie.id), "role": "viewer"})

    # 创建 Alice 的私有仓库
    repo_alice = Repository(
        owner="alice-org",
        name="alice-private-repo",
        full_name="alice-org/alice-private-repo",
        created_by=user_alice.id,
        is_public=False,
    )
    perm_db_session.add(repo_alice)
    perm_db_session.commit()

    # Charlie 作为只读协作者加入 Alice 仓库
    member_charlie = RepositoryMember(
        repo_id=repo_alice.id,
        user_id=user_charlie.id,
        role="viewer",
    )
    perm_db_session.add(member_charlie)

    # 创建 Alice 仓库下的安全草稿
    draft_alice = ActionDraft(
        repo_id=repo_alice.id,
        draft_type="create_issue",
        target_type="repository",
        title="Alice Confidential Draft",
        content="Secret draft content",
        risk_level="medium",
        status="pending_confirmation",
        created_by=user_alice.id,
        meta={"some": "metadata"},
    )
    perm_db_session.add(draft_alice)
    perm_db_session.commit()

    # 1. 无关开发者 Bob 尝试列出草稿列表：结果中绝不可出现 Alice 的私有草稿
    resp_list_bob = perm_client.get(
        "/api/action-drafts",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_list_bob.status_code == 200
    returned_ids = [d["id"] for d in resp_list_bob.json()]
    assert str(draft_alice.id) not in returned_ids

    # 2. 无关开发者 Bob 尝试直接读取 Alice 私有草稿详情：拦截 403 或 404
    resp_get_bob = perm_client.get(
        f"/api/action-drafts/{draft_alice.id}",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_get_bob.status_code in (403, 404)

    # 3. 无关开发者 Bob 尝试取消 Alice 私有草稿：拦截 403 或 404
    resp_cancel_bob = perm_client.post(
        f"/api/action-drafts/{draft_alice.id}/cancel",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_cancel_bob.status_code in (403, 404)

    # 4. 无关开发者 Bob 尝试跨库修改 Alice 仓库的项目索引 snooze 状态：拦截 403 或 404
    resp_snooze_bob = perm_client.post(
        f"/api/repos/{repo_alice.id}/memory-index/snooze",
        json={"days": 14},
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_snooze_bob.status_code in (403, 404)

    # 5. 无关开发者 Bob 尝试多仓库周报探测 Alice 仓库：拦截 403 或 404
    resp_report_bob = perm_client.post(
        "/api/workspaces/multi-repo-report",
        json={
            "repo_ids": [str(repo_alice.id)],
            "start_date": "2026-01-01",
            "end_date": "2026-01-07",
        },
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_report_bob.status_code in (403, 404)

    # 6. Viewer Charlie 能够只读查看草稿详情，但无法取消或执行草稿
    resp_get_charlie = perm_client.get(
        f"/api/action-drafts/{draft_alice.id}",
        headers={"Authorization": f"Bearer {token_charlie}"},
    )
    assert resp_get_charlie.status_code == 200
    assert resp_get_charlie.json()["id"] == str(draft_alice.id)

    resp_cancel_charlie = perm_client.post(
        f"/api/action-drafts/{draft_alice.id}/cancel",
        headers={"Authorization": f"Bearer {token_charlie}"},
    )
    assert resp_cancel_charlie.status_code == 403

    resp_confirm_charlie = perm_client.post(
        f"/api/action-drafts/{draft_alice.id}/confirm",
        headers={"Authorization": f"Bearer {token_charlie}"},
    )
    assert resp_confirm_charlie.status_code == 403

    # 7. Alice 能够成功取消自己的草稿
    resp_cancel_alice = perm_client.post(
        f"/api/action-drafts/{draft_alice.id}/cancel",
        headers={"Authorization": f"Bearer {token_alice}"},
    )
    assert resp_cancel_alice.status_code == 200
    assert resp_cancel_alice.json()["status"] == "cancelled"


def test_evaluations_authorization_and_isolation(perm_client, perm_db_session, monkeypatch):
    import uuid
    import app.api.routes.evaluations as eval_routes
    from app.db.models import EvalRun

    # 1. 准备用户和私有仓库
    user_alice = User(
        name="alice_eval",
        email="alice_eval@example.com",
        role="developer",
        hashed_password=hash_password("Pass123!"),
    )
    user_bob = User(
        name="bob_eval",
        email="bob_eval@example.com",
        role="developer",
        hashed_password=hash_password("Pass123!"),
    )
    user_admin = User(
        name="admin_eval",
        email="admin_eval@example.com",
        role="owner",
        hashed_password=hash_password("Pass123!"),
    )
    perm_db_session.add_all([user_alice, user_bob, user_admin])
    perm_db_session.commit()

    repo_alice = Repository(
        owner="alice",
        name="private-eval-repo",
        full_name="alice/private-eval-repo",
        created_by=user_alice.id,
        is_public=False,
    )
    perm_db_session.add(repo_alice)
    perm_db_session.commit()

    token_alice = create_access_token(data={"sub": str(user_alice.id), "role": "developer"})
    token_bob = create_access_token(data={"sub": str(user_bob.id), "role": "developer"})
    token_admin = create_access_token(data={"sub": str(user_admin.id), "role": "owner"})

    # Mock 外部 evaluator 与后台任务，避免真实执行模型或向量检索
    async def fake_basic_eval(db, repo_id=None):
        return {
            "target": {"name": "BasicEval", "repo_id": str(repo_id)}
            if repo_id
            else {"name": "BasicEval"},
            "repo_id": str(repo_id) if repo_id else None,
            "issue_classification_accuracy": 0.95,
            "tool_call_accuracy": 0.98,
        }

    async def fake_rescore_job(*args, **kwargs):
        pass

    monkeypatch.setattr(eval_routes, "run_basic_eval", fake_basic_eval)
    monkeypatch.setattr(eval_routes, "_execute_rescore_job", fake_rescore_job)

    # 2. 非管理员运行未绑定 repo_id 的评测必须拒绝 (HTTP 400)
    resp_no_repo = perm_client.post(
        "/api/evals/run",
        json={"name": "unscoped_run"},
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_no_repo.status_code == 400
    assert "必须指定 repo_id" in resp_no_repo.json()["detail"]

    # 3. 无关开发者 Bob 尝试在 Alice 私有仓库运行评测：拦截 403
    resp_run_bob = perm_client.post(
        "/api/evals/run",
        json={"name": "bob_probe", "repo_id": str(repo_alice.id)},
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_run_bob.status_code == 403

    # 4. Alice 成功运行评测，且结果中持久化了作用域 repo_id 与 target.repo_id
    resp_run_alice = perm_client.post(
        "/api/evals/run",
        json={"name": "alice_eval", "repo_id": str(repo_alice.id)},
        headers={"Authorization": f"Bearer {token_alice}"},
    )
    assert resp_run_alice.status_code == 200
    eval_id_alice = resp_run_alice.json()["eval_id"]
    eval_row = perm_db_session.get(EvalRun, uuid.UUID(eval_id_alice))
    assert eval_row is not None
    assert eval_row.result_json.get("repo_id") == str(repo_alice.id)
    assert (eval_row.result_json.get("target") or {}).get("repo_id") == str(repo_alice.id)

    # 5. Bob 查看评测列表不应看到 Alice 的私有评测
    resp_list_bob = perm_client.get(
        "/api/evals",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_list_bob.status_code == 200
    bob_eval_ids = [item["eval_id"] for item in resp_list_bob.json()]
    assert eval_id_alice not in bob_eval_ids

    # 6. Bob 直接查询 Alice 的评测详情：严格拦截 403
    resp_get_bob = perm_client.get(
        f"/api/evals/{eval_id_alice}",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_get_bob.status_code == 403

    # 7. Alice 和 Admin 均可正常查看该评测
    resp_get_alice = perm_client.get(
        f"/api/evals/{eval_id_alice}",
        headers={"Authorization": f"Bearer {token_alice}"},
    )
    assert resp_get_alice.status_code == 200
    assert resp_get_alice.json()["eval_id"] == eval_id_alice

    resp_get_admin = perm_client.get(
        f"/api/evals/{eval_id_alice}",
        headers={"Authorization": f"Bearer {token_admin}"},
    )
    assert resp_get_admin.status_code == 200

    # 8. 评测对比 (compare) 权限验证：Bob 尝试对比 Alice 的评测被拦截 403
    eval_run_alice_2 = EvalRun(
        name="alice_eval_2",
        result_json={
            "repo_id": str(repo_alice.id),
            "target": {"name": "ChatAgent", "repo_id": str(repo_alice.id)},
            "summary_metrics": {"faithfulness": 0.8},
            "evaluation_set": {"sha256": "abc"},
        },
    )
    perm_db_session.add(eval_run_alice_2)
    perm_db_session.commit()

    resp_compare_bob = perm_client.get(
        f"/api/evals/compare?base_eval_id={eval_id_alice}&candidate_eval_id={eval_run_alice_2.id}",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_compare_bob.status_code == 403

    resp_compare_alice = perm_client.get(
        f"/api/evals/compare?base_eval_id={eval_id_alice}&candidate_eval_id={eval_run_alice_2.id}",
        headers={"Authorization": f"Bearer {token_alice}"},
    )
    assert resp_compare_alice.status_code == 200

    # 9. 评测重评分 (rescore) 权限验证：Bob 尝试重评分 Alice 的评测被拦截 403，不得创建任务
    eval_with_error = EvalRun(
        name="alice_rescore_source",
        result_json={
            "repo_id": str(repo_alice.id),
            "target": {"name": "ChatAgent", "repo_id": str(repo_alice.id)},
            "cases": [
                {
                    "id": "c1",
                    "ragas_errors": [{"metric": "faithfulness", "error": "timeout"}],
                }
            ],
        },
    )
    perm_db_session.add(eval_with_error)
    perm_db_session.commit()

    initial_eval_count = perm_db_session.query(EvalRun).count()
    resp_rescore_bob = perm_client.post(
        f"/api/evals/{eval_with_error.id}/rescore",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_rescore_bob.status_code == 403
    assert perm_db_session.query(EvalRun).count() == initial_eval_count  # 未越权创建任务

    resp_rescore_alice = perm_client.post(
        f"/api/evals/{eval_with_error.id}/rescore",
        headers={"Authorization": f"Bearer {token_alice}"},
    )
    assert resp_rescore_alice.status_code == 200
    assert resp_rescore_alice.json()["status"] == "queued"

    # 10. 历史无作用域/模糊数据严格防泄漏 (Fail-Closed)
    unscoped_eval = EvalRun(
        name="legacy_unscoped_eval",
        result_json={"summary_metrics": {"accuracy": 0.5}},
    )
    perm_db_session.add(unscoped_eval)
    perm_db_session.commit()

    # 普通开发者 Bob 列表看不到无作用域评测，且直接查询返回 403
    resp_list_bob2 = perm_client.get("/api/evals", headers={"Authorization": f"Bearer {token_bob}"})
    assert str(unscoped_eval.id) not in [item["eval_id"] for item in resp_list_bob2.json()]

    resp_get_unscoped_bob = perm_client.get(
        f"/api/evals/{unscoped_eval.id}",
        headers={"Authorization": f"Bearer {token_bob}"},
    )
    assert resp_get_unscoped_bob.status_code == 403

    # 管理员 Admin 可以正常查看无作用域历史评测
    resp_get_unscoped_admin = perm_client.get(
        f"/api/evals/{unscoped_eval.id}",
        headers={"Authorization": f"Bearer {token_admin}"},
    )
    assert resp_get_unscoped_admin.status_code == 200
