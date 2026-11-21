from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_access_token
from app.db.models import AuditLog, Repository, RepositoryMember, User
from app.db.session import get_db

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "owner": {
        "repo:read",
        "repo:sync",
        "agent:run",
        "draft:create",
        "draft:approve",
        "eval:read",
        "settings:manage",
    },
    "maintainer": {
        "repo:read",
        "repo:sync",
        "agent:run",
        "draft:create",
        "draft:approve",
        "eval:read",
    },
    "developer": {"repo:read", "agent:run", "draft:create", "eval:read"},
    "viewer": {"repo:read", "eval:read"},
}


def ensure_demo_user(db: Session) -> User:
    user = db.query(User).order_by(User.created_at.asc()).first()
    if user is None:
        user = User(name="demo-owner", role="owner")
        db.add(user)
        db.commit()
        db.refresh(user)
    if not getattr(user, "role", None):
        user.role = "owner"
        db.commit()
        db.refresh(user)
    return user


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        payload = decode_access_token(token)
        if not payload or "sub" not in payload:
            raise HTTPException(
                status_code=401,
                detail="无效或已过期的访问令牌",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            user_id = UUID(str(payload["sub"]))
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=401,
                detail="访问令牌包含无效的用户标识",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user = db.get(User, user_id)
        if user is None or not getattr(user, "is_active", True):
            raise HTTPException(
                status_code=401,
                detail="用户不存在或账号已被停用",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    # 未提供 Bearer 令牌
    if settings.auth_required or not settings.demo_mode or settings.is_production:
        raise HTTPException(
            status_code=401,
            detail="需要身份认证",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 开发/演示降级兼容
    return ensure_demo_user(db)


def resolve_user(user: Any, db: Session) -> User:
    if isinstance(user, User):
        return user
    if settings.auth_required or not settings.demo_mode or settings.is_production:
        raise HTTPException(
            status_code=401,
            detail="需要身份认证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return ensure_demo_user(db)


def has_permission(user: User, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get((user.role or "viewer").lower(), set())


def require_permission(permission: str) -> Callable:
    def dependency(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user, permission):
            raise HTTPException(status_code=403, detail=f"需要权限：{permission}")
        return user

    return dependency


def check_repo_access(
    db: Session,
    user: User,
    repo_id: UUID,
    required_permission: str = "repo:read",
) -> Repository:
    repo = db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="未找到仓库")

    # 1. 系统超级管理员 (owner)
    if (user.role or "").lower() == "owner":
        return repo

    # 2. 仓库创建者
    if repo.created_by and repo.created_by == user.id:
        return repo

    # 3. 仓库成员
    member = (
        db.query(RepositoryMember)
        .filter(RepositoryMember.repo_id == repo.id, RepositoryMember.user_id == user.id)
        .first()
    )
    if member and has_permission(User(role=member.role), required_permission):
        return repo

    # 4. 公开仓库只读访问
    if required_permission in ("repo:read", "eval:read") and getattr(repo, "is_public", False):
        return repo

    raise HTTPException(status_code=403, detail="无权访问该仓库")


def filter_user_repositories(db: Session, user: User) -> list[Repository]:
    base_query = db.query(Repository).filter(Repository.provider != "knowledge_base")
    if (user.role or "").lower() in ("owner", "maintainer"):
        return base_query.order_by(Repository.updated_at.desc()).all()

    member_repo_ids = [
        m.repo_id
        for m in db.query(RepositoryMember.repo_id)
        .filter(RepositoryMember.user_id == user.id)
        .all()
    ]
    query = base_query.filter(
        or_(
            Repository.created_by == user.id,
            Repository.id.in_(member_repo_ids) if member_repo_ids else False,
            Repository.is_public == True,
            # 如果仓库没有标记 created_by 且不是私有，默认可见
            (Repository.created_by.is_(None)) & (Repository.is_public == True),
        )
    )
    return query.order_by(Repository.updated_at.desc()).all()


def write_audit_log(
    db: Session,
    *,
    user: User | None,
    repo_id=None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    status: str = "success",
    request_json: dict | None = None,
    result_json: dict | None = None,
) -> AuditLog:
    row = AuditLog(
        user_id=user.id if user else None,
        repo_id=repo_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        status=status,
        request_json=request_json or {},
        result_json=result_json or {},
    )
    db.add(row)
    db.flush()
    return row
