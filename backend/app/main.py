import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, text

from app.api.routes import (
    action_drafts,
    auth,
    chat,
    ci,
    code_graph,
    conversations,
    evaluations,
    feedback,
    issues,
    knowledge,
    project_index,
    pull_requests,
    rag,
    repos,
    reports,
    search,
    skills,
    webhooks,
    workspaces,
)
from app.core.config import settings
from app.db.migration import run_migrations
from app.db.models import Base
from app.db.session import engine
from app.services.scheduler import periodic_repo_sync_loop
from app.services.rag.vector_store import MilvusUnavailableError


def _cors_origins() -> list[str]:
    return [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]


def _cors_origin_regex() -> str | None:
    if settings.app_env.lower() == "production":
        return None
    return r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0")

    @app.exception_handler(MilvusUnavailableError)
    async def milvus_unavailable_handler(_request, exc: MilvusUnavailableError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc), "service": "milvus", "status": "degraded"},
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_origin_regex=_cors_origin_regex(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(repos.router, prefix="/api/repos", tags=["repos"])
    app.include_router(conversations.router, prefix="/api/repos", tags=["conversations"])
    app.include_router(workspaces.router, prefix="/api/workspaces", tags=["workspaces"])
    app.include_router(issues.router, prefix="/api", tags=["issues"])
    app.include_router(pull_requests.router, prefix="/api", tags=["pull_requests"])
    app.include_router(ci.router, prefix="/api", tags=["ci"])
    app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
    app.include_router(feedback.router, prefix="/api/chat/feedback", tags=["chat_feedback"])
    app.include_router(action_drafts.router, prefix="/api/action-drafts", tags=["action_drafts"])
    app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
    app.include_router(search.router, prefix="/api/search", tags=["search"])
    app.include_router(skills.router, prefix="/api/skills", tags=["skills"])
    app.include_router(knowledge.router, prefix="/api/repos", tags=["knowledge"])
    app.include_router(project_index.router, prefix="/api/repos", tags=["project_index"])
    app.include_router(rag.router, prefix="/api/rag", tags=["rag"])
    app.include_router(code_graph.router, prefix="/api", tags=["code_graph"])
    app.include_router(evaluations.router, prefix="/api/evals", tags=["evaluations"])
    app.include_router(webhooks.router, prefix="/api/webhooks", tags=["webhooks"])

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "app": settings.app_name}

    @app.on_event("startup")
    async def initialize_database_and_services() -> None:
        # 生产环境安全配置校验
        settings.validate_security_settings()

        # 统一使用 Alembic 迁移体系执行建表、升级与存量库纳管
        try:
            run_migrations(bind=engine)
        except Exception as exc:
            if settings.is_production:
                raise
            import logging

            logging.getLogger(__name__).warning(f"启动期数据库自动迁移跳过或失败: {exc}")

        from app.db.session import SessionLocal
        from app.services.conversations import backfill_all_default_conversations
        from app.services.code_analysis import purge_legacy_code_documents
        from app.services.rag.vector_store import delete_milvus_documents, milvus_runtime_status

        try:
            db = SessionLocal()
            try:
                backfill_all_default_conversations(db)
                if purge_legacy_code_documents(db):
                    db.commit()
            finally:
                db.close()
        except Exception as exc:
            if settings.is_production:
                raise
            import logging

            logging.getLogger(__name__).warning(f"启动期默认会话补全或清理跳过: {exc}")
        app.state.rag_vector_status = milvus_runtime_status()
        delete_milvus_documents(source_types=["code_file"], strict=False)
        if settings.auto_sync_enabled:
            app.state.repo_sync_task = asyncio.create_task(periodic_repo_sync_loop())

    @app.on_event("shutdown")
    async def stop_background_tasks() -> None:
        task = getattr(app.state, "repo_sync_task", None)
        if task:
            task.cancel()

    return app


app = create_app()
