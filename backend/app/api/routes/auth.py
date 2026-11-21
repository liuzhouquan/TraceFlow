from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import TokenResponse, UserLoginRequest, UserRegisterRequest, UserResponse
from app.services.permissions import get_current_user

router = APIRouter()


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserRegisterRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    email = payload.email.strip().lower()
    name = payload.name.strip()

    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="该邮箱已存在或已被注册")

    if db.query(User).filter(User.name == name).first():
        raise HTTPException(status_code=400, detail="该用户名已存在")

    requested_role = (payload.role or "developer").strip().lower()
    if requested_role in ("owner", "maintainer"):
        raise HTTPException(
            status_code=400,
            detail="普通用户注册不允许指定特权角色 (owner/maintainer)",
        )

    role = requested_role if requested_role in ("developer", "viewer") else "developer"

    user = User(
        name=name,
        email=email,
        hashed_password=hash_password(payload.password),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(data={"sub": str(user.id), "role": user.role, "name": user.name})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user=UserResponse.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: UserLoginRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    identifier = payload.username.strip().lower()
    raw_identifier = payload.username.strip()

    user = db.query(User).filter(or_(User.email == identifier, User.name == raw_identifier)).first()

    if (
        user is None
        or not user.hashed_password
        or not verify_password(payload.password, user.hashed_password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not getattr(user, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号已被停用，请联系管理员",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(data={"sub": str(user.id), "role": user.role, "name": user.name})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(user)
