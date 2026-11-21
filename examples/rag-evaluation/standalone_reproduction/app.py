from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import BASE_DIR, Settings, load_settings
from control_plane import ControlPlaneError, IdempotencyConflict
from document_loader import SUPPORTED_SUFFIXES
from evidence_workflow import CONTRACTS, investigate
from model_client import ModelClient, ModelServiceError
from rag_pipeline import RAGPipeline
from ragas_evaluation import (
    EvaluationRunStore,
    evaluate_generation_quality,
)
from rag_store import RAGStore, VectorStoreError
from release_service import ReleaseService
from repositories import REPOSITORIES, get_repository, settings_for_repository


settings = load_settings()
runtimes: dict[str, tuple[RAGStore, RAGPipeline, ReleaseService]] = {}
background_jobs: set[asyncio.Task[None]] = set()


def _runtime(repo_id: str) -> tuple[RAGStore, RAGPipeline, ReleaseService]:
    if get_repository(repo_id) is None:
        raise HTTPException(status_code=404, detail="没有找到这个仓库")
    runtime = runtimes.get(repo_id)
    if runtime is not None:
        return runtime
    scoped_settings = settings_for_repository(settings, repo_id)
    store = RAGStore(scoped_settings, repo_id, snapshot_mode=True)
    models = ModelClient(scoped_settings)
    pipeline = RAGPipeline(scoped_settings, store, models)
    releases = ReleaseService(scoped_settings, store, models, pipeline)
    runtime = (store, pipeline, releases)
    runtimes[repo_id] = runtime
    return runtime


def _remember(task: asyncio.Task[None]) -> None:
    background_jobs.add(task)
    task.add_done_callback(background_jobs.discard)


@asynccontextmanager
async def lifespan(_: FastAPI):
    for repository in REPOSITORIES:
        _store, _pipeline, releases = _runtime(repository.id)
        await releases.recover_interrupted_jobs()
        try:
            await releases.reconcile()
        except Exception:
            # Status and the explicit reconcile endpoint will expose the dependency error.
            pass
    yield
    if background_jobs:
        await asyncio.gather(*background_jobs, return_exceptions=True)
    await asyncio.gather(
        *(asyncio.to_thread(store.close) for store, _pipeline, _releases in runtimes.values())
    )


app = FastAPI(
    title="第 9 章 Production RAG 工作室",
    description="在进阶版 RAG 检索链路上加入版本构建、质量门禁、Alias 发布、回滚和对账",
    version="3.0.0",
    lifespan=lifespan,
)

static_dir = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=settings.top_k, ge=1, le=20)
    mode: Literal["dense", "hybrid", "hybrid_rerank"] = settings.retrieval_mode


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=settings.top_k, ge=1, le=20)
    mode: Literal["dense", "hybrid", "hybrid_rerank"] = settings.retrieval_mode


class BuildRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, max_length=200)
    auto_promote: bool = True


class InvestigationRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    contract: str = "release_conflict"
    mode: Literal["dense", "hybrid", "hybrid_rerank"] = settings.retrieval_mode


class RagasEvaluationRequest(BaseModel):
    run_judge: bool | None = None
    top_k: int = Field(default=settings.release_top_k, ge=1, le=20)
    mode: Literal["dense", "hybrid", "hybrid_rerank"] = "hybrid"


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, IdempotencyConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (ModelServiceError, VectorStoreError)):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, (ValueError, ControlPlaneError)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=f"处理失败：{exc}")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/api/repositories")
async def list_repositories() -> list[dict[str, str]]:
    return [repository.public_dict() for repository in REPOSITORIES]


@app.get("/api/repositories/{repo_id}/status")
async def get_status(repo_id: str) -> dict[str, Any]:
    store, pipeline, releases = _runtime(repo_id)
    result = await pipeline.status()
    result["repo_id"] = repo_id
    result["supported_files"] = sorted(SUPPORTED_SUFFIXES)
    release_status = releases.status()
    active_release = release_status.get("active")
    result["document_count"] = len(store.list_documents())
    result["chunk_count"] = int(active_release.get("chunk_count") or 0) if active_release else 0
    result["release"] = release_status
    result["version"] = "3.0.0"
    return result


@app.get("/api/repositories/{repo_id}/documents")
async def list_documents(repo_id: str) -> list[dict[str, Any]]:
    store, _pipeline, _releases = _runtime(repo_id)
    return [document.public_dict() for document in store.list_documents()]


@app.post("/api/repositories/{repo_id}/documents", status_code=201)
async def upload_document(repo_id: str, file: Annotated[UploadFile, File(...)]) -> dict[str, Any]:
    _store, _pipeline, releases = _runtime(repo_id)
    data = await file.read(releases.settings.max_upload_bytes + 1)
    try:
        document, changed = await releases.stage_bytes(
            filename=file.filename or "upload.txt",
            data=data,
            content_type=file.content_type or "",
        )
        return {
            **document.public_dict(),
            "created": changed,
            "pending_release": changed,
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/repositories/{repo_id}/documents/{document_id}/chunks")
async def list_document_chunks(repo_id: str, document_id: str) -> list[dict[str, Any]]:
    store, _pipeline, _releases = _runtime(repo_id)
    if not store.get_document(document_id):
        raise HTTPException(status_code=404, detail="没有找到这份文档")
    try:
        return await asyncio.to_thread(store.list_chunks, document_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.delete("/api/repositories/{repo_id}/documents/{document_id}")
async def delete_document(repo_id: str, document_id: str) -> dict[str, Any]:
    _store, _pipeline, releases = _runtime(repo_id)
    try:
        document = await releases.stage_delete(document_id)
    except Exception as exc:
        raise _http_error(exc) from exc
    if not document:
        raise HTTPException(status_code=404, detail="没有找到这份文档")
    return {"id": document_id, "deleted": False, "pending_delete": True}


@app.post("/api/repositories/{repo_id}/seed")
async def seed_documents(repo_id: str) -> dict[str, Any]:
    _store, _pipeline, releases = _runtime(repo_id)
    try:
        return await releases.seed_samples()
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/repositories/{repo_id}/reindex", status_code=410)
async def deprecated_reindex(repo_id: str) -> None:
    _runtime(repo_id)
    raise HTTPException(
        status_code=410,
        detail="RAG 工程化阶段不再原地重建 Active Collection，请使用当前仓库的 /releases/build",
    )


@app.post("/api/repositories/{repo_id}/releases/build", status_code=status.HTTP_202_ACCEPTED)
async def build_release(repo_id: str, payload: BuildRequest) -> dict[str, Any]:
    _store, _pipeline, releases = _runtime(repo_id)
    try:
        key = (payload.idempotency_key or "").strip() or f"build-{uuid.uuid4()}"
        job, created = releases.create_build_job(
            idempotency_key=key,
            auto_promote=payload.auto_promote,
        )
        if created:
            _remember(asyncio.create_task(releases.run_build_job(job.id)))
        return {
            **asdict(job),
            "created": created,
            "replayed": not created,
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/repositories/{repo_id}/releases")
async def list_releases(repo_id: str) -> list[dict[str, Any]]:
    _store, _pipeline, releases = _runtime(repo_id)
    return releases.list_releases()


@app.post("/api/repositories/{repo_id}/releases/{generation_id}/promote")
async def promote_release(repo_id: str, generation_id: str) -> dict[str, Any]:
    _store, _pipeline, releases = _runtime(repo_id)
    try:
        return await releases.promote(generation_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/repositories/{repo_id}/releases/{generation_id}/rollback")
async def rollback_release(repo_id: str, generation_id: str) -> dict[str, Any]:
    _store, _pipeline, releases = _runtime(repo_id)
    try:
        return await releases.rollback(generation_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/repositories/{repo_id}/jobs")
async def list_jobs(repo_id: str) -> list[dict[str, Any]]:
    _store, _pipeline, releases = _runtime(repo_id)
    return [asdict(job) for job in releases.control.list_jobs()]


@app.get("/api/repositories/{repo_id}/jobs/{job_id}")
async def get_job(repo_id: str, job_id: str) -> dict[str, Any]:
    _store, _pipeline, releases = _runtime(repo_id)
    result = releases.job_detail(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job 不存在")
    return result


@app.post("/api/repositories/{repo_id}/reconcile")
async def reconcile_release(repo_id: str) -> dict[str, Any]:
    _store, _pipeline, releases = _runtime(repo_id)
    try:
        return await releases.reconcile()
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/evidence-contracts")
async def evidence_contracts() -> dict[str, list[str]]:
    return {"contracts": sorted(CONTRACTS)}


@app.post("/api/repositories/{repo_id}/investigate")
async def investigate_question(repo_id: str, payload: InvestigationRequest) -> dict[str, Any]:
    _store, pipeline, releases = _runtime(repo_id)
    try:
        return await investigate(
            settings=releases.settings,
            pipeline=pipeline,
            question=payload.question,
            contract_name=payload.contract,
            mode=payload.mode,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/repositories/{repo_id}/search")
async def search_documents(repo_id: str, payload: SearchRequest) -> dict[str, Any]:
    _store, pipeline, _releases = _runtime(repo_id)
    try:
        outcome = await pipeline.retrieve(payload.query, payload.top_k, payload.mode)
        public_results = [
            item.public_dict(index)
            for index, item in enumerate(outcome.results, start=1)
        ]
        return {
            "query": payload.query.strip(),
            "mode": payload.mode,
            "results": public_results,
            "result_count": len(public_results),
            "retrieval": outcome.trace,
            "took_ms": outcome.took_ms,
        }
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/api/repositories/{repo_id}/ask")
async def ask_question(repo_id: str, payload: AskRequest) -> dict[str, Any]:
    _store, pipeline, _releases = _runtime(repo_id)
    try:
        return await pipeline.ask(payload.question, payload.top_k, payload.mode)
    except Exception as exc:
        raise _http_error(exc) from exc


async def _run_ragas_evaluation(
    *,
    run_id: str,
    run_store: EvaluationRunStore,
    scoped_settings: Settings,
    pipeline: RAGPipeline,
    run_judge: bool,
    top_k: int,
    mode: str,
) -> None:
    run_store.update(run_id, status="running")
    try:
        result = await evaluate_generation_quality(
            settings=scoped_settings,
            pipeline=pipeline,
            run_judge=run_judge,
            top_k=top_k,
            mode=mode,
        )
        run_store.update(run_id, status=str(result["status"]), result=result)
    except Exception as exc:
        run_store.update(
            run_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )


@app.post(
    "/api/repositories/{repo_id}/evaluations/ragas",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_ragas_evaluation(
    repo_id: str,
    payload: RagasEvaluationRequest,
) -> dict[str, Any]:
    _store, pipeline, releases = _runtime(repo_id)
    scoped_settings = releases.settings
    run_judge = scoped_settings.ragas_enabled if payload.run_judge is None else payload.run_judge
    run_store = EvaluationRunStore(scoped_settings)
    run = run_store.create(
        run_judge=run_judge,
        top_k=payload.top_k,
        mode=payload.mode,
    )
    task = asyncio.create_task(
        _run_ragas_evaluation(
            run_id=str(run["run_id"]),
            run_store=run_store,
            scoped_settings=scoped_settings,
            pipeline=pipeline,
            run_judge=run_judge,
            top_k=payload.top_k,
            mode=payload.mode,
        )
    )
    _remember(task)
    return run


@app.get("/api/repositories/{repo_id}/evaluations/ragas")
async def list_ragas_evaluations(repo_id: str, limit: int = 20) -> list[dict[str, Any]]:
    _store, _pipeline, releases = _runtime(repo_id)
    return EvaluationRunStore(releases.settings).list(limit)


@app.get("/api/repositories/{repo_id}/evaluations/ragas/{run_id}")
async def get_ragas_evaluation(repo_id: str, run_id: str) -> dict[str, Any]:
    _store, _pipeline, releases = _runtime(repo_id)
    try:
        return EvaluationRunStore(releases.settings).get(run_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/api/sample-questions")
async def sample_questions() -> dict[str, list[str]]:
    path = BASE_DIR / "sample_docs" / "sample_questions.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        questions = [str(item) for item in value["questions"]]
        questions.append("新旧发布资料中的核心接口错误率回滚阈值是否冲突？")
        return {"questions": questions}
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"示例问题读取失败：{exc}") from exc
