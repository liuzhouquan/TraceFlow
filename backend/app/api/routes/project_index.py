import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import Repository, User
from app.db.session import get_db
from app.schemas.project_index import (
    ProjectIndexScanResponse,
    ProjectIndexSnoozeRequest,
    ProjectIndexSnoozeResponse,
    ProjectIndexStateResponse,
)
from app.services.permissions import check_repo_access, get_current_user, resolve_user
from app.services.project_indexing import (
    get_project_index_state,
    mark_project_index_building,
    scan_project_index_by_id,
    snooze_project_index,
)

router = APIRouter()


def _repo_or_404(db: Session, repo_id: uuid.UUID) -> Repository:
    repo = db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="未找到仓库")
    return repo


@router.get("/{repo_id}/memory-index/state", response_model=ProjectIndexStateResponse)
async def get_memory_index_state(
    repo_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    repo = _repo_or_404(db, repo_id)
    user = resolve_user(current_user, db)
    check_repo_access(db, user, repo_id, "repo:read")
    return get_project_index_state(db, repo)


@router.post(
    "/{repo_id}/memory-index/scan", response_model=ProjectIndexScanResponse, status_code=202
)
async def start_memory_index_scan(
    repo_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectIndexScanResponse:
    repo = _repo_or_404(db, repo_id)
    user = resolve_user(current_user, db)
    check_repo_access(db, user, repo_id, "agent:run")
    mark_project_index_building(db, repo)
    background_tasks.add_task(scan_project_index_by_id, repo.id)
    return ProjectIndexScanResponse(repo_id=repo.id, started=True, status="building")


@router.post("/{repo_id}/memory-index/snooze", response_model=ProjectIndexSnoozeResponse)
async def snooze_memory_index(
    repo_id: uuid.UUID,
    payload: ProjectIndexSnoozeRequest | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectIndexSnoozeResponse:
    repo = _repo_or_404(db, repo_id)
    user = resolve_user(current_user, db)
    check_repo_access(db, user, repo_id, "agent:run")
    row = snooze_project_index(db, repo, days=(payload.days if payload else 7))
    return ProjectIndexSnoozeResponse(repo_id=repo.id, snoozed_until=row.snoozed_until)
