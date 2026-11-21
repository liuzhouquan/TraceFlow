from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from control_plane import (
    ControlPlane,
    Generation,
    Job,
    ManifestChunk,
    ManifestDocument,
)
from document_loader import load_document
from model_client import ModelClient
from quality_gate import evaluate_release
from rag_pipeline import RAGPipeline, _safe_filename
from rag_store import DocumentRecord, MilvusVectorStore, RAGStore
from reranker import Reranker
from config import BASE_DIR, Settings


def _canonical_hash(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


class ReleaseService:
    """Build immutable Milvus generations while SQLite stores only control metadata."""

    def __init__(
        self,
        settings: Settings,
        store: RAGStore,
        models: ModelClient,
        pipeline: RAGPipeline,
        *,
        control: ControlPlane | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.models = models
        self.pipeline = pipeline
        self.control = control or ControlPlane(settings.catalog_path)
        self.reranker = Reranker(settings)
        self.eval_cases_path = BASE_DIR / "eval_cases" / "held_out_v1.jsonl"
        self._build_lock = asyncio.Lock()
        self._release_lock = asyncio.Lock()

    def _config_snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": self.settings.index_schema_version,
            "chunk_size": self.settings.chunk_size,
            "chunk_overlap": self.settings.chunk_overlap,
            "embedding_provider": self.models.embedding_provider,
            "embedding_model": self.models.embedding_model,
            "embedding_dimensions": self.settings.embedding_dimensions,
            "embedding_query_instruction": self.settings.embedding_query_instruction,
            "repo_id": self.store.repo_id,
            "retrieval_policy": {
                "serving_mode": self.settings.retrieval_mode,
                "quality_gate_mode": "hybrid",
                "dense_candidate_k": self.settings.dense_candidate_k,
                "keyword_candidate_k": self.settings.keyword_candidate_k,
                "rerank_candidate_k": self.settings.rerank_candidate_k,
                "rrf_k": self.settings.rrf_k,
                "min_relevance_score": self.settings.min_relevance_score,
                "rerank_provider": self.settings.rerank_provider,
                "rerank_model": self.settings.rerank_model,
                "rerank_instruction": self.settings.rerank_instruction,
                "rerank_min_score": self.settings.rerank_min_score,
                "context_chunk_max_chars": self.settings.context_chunk_max_chars,
            },
            "quality_policy": {
                "release_top_k": self.settings.release_top_k,
                "recall_at_k_min": self.settings.quality_recall_at_k_min,
                "mrr_min": self.settings.quality_mrr_min,
                "ndcg_at_k_min": self.settings.quality_ndcg_at_k_min,
                "negative_rejection_min": self.settings.quality_negative_rejection_min,
                "regression_tolerance": self.settings.quality_regression_tolerance,
            },
        }

    def _dataset_snapshot(self) -> dict[str, str]:
        data = self.eval_cases_path.read_bytes()
        return {
            "name": self.eval_cases_path.name,
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def _source_snapshot(self) -> list[dict[str, Any]]:
        documents = self.store.catalog.list_desired_documents()
        return [
            {
                "id": document.id,
                "name": document.name,
                "stored_path": document.stored_path,
                "content_type": document.content_type,
                "checksum": document.checksum,
                "file_size": document.file_size,
                "character_count": document.character_count,
            }
            for document in documents
        ]

    async def stage_bytes(
        self,
        *,
        filename: str,
        data: bytes,
        content_type: str = "",
    ) -> tuple[DocumentRecord, bool]:
        if not data:
            raise ValueError("上传文件为空")
        if len(data) > self.settings.max_upload_bytes:
            limit_mb = self.settings.max_upload_bytes // (1024 * 1024)
            raise ValueError(f"文件不能超过 {limit_mb} MB")
        safe_name = _safe_filename(filename)
        loaded = load_document(safe_name, data)
        if not loaded.text.strip():
            raise ValueError("文档没有可索引的正文")
        checksum = hashlib.sha256(data).hexdigest()
        stored_path = self.settings.upload_dir / f"{checksum[:12]}-{safe_name}"
        await asyncio.to_thread(stored_path.write_bytes, data)
        return await asyncio.to_thread(
            self.store.catalog.stage_document,
            name=safe_name,
            stored_path=str(stored_path),
            content_type=content_type or "application/octet-stream",
            checksum=checksum,
            file_size=len(data),
            character_count=len(loaded.text),
        )

    async def stage_delete(self, document_id: str) -> DocumentRecord | None:
        return await asyncio.to_thread(self.store.catalog.stage_delete, document_id)

    async def seed_samples(self) -> dict[str, Any]:
        sample_dir = BASE_DIR / "sample_docs"
        imported = 0
        duplicates = 0
        documents = []
        for path in sorted(sample_dir.glob("[0-9][0-9]_*.md")):
            document, created = await self.stage_bytes(
                filename=path.name,
                data=path.read_bytes(),
                content_type="text/markdown",
            )
            imported += int(created)
            duplicates += int(not created)
            documents.append(document.public_dict())
        return {
            "imported": imported,
            "duplicates": duplicates,
            "documents": documents,
            "pending_release": imported > 0,
        }

    def _build_request(self) -> dict[str, Any]:
        documents = self._source_snapshot()
        if not documents:
            raise ValueError("没有待构建的文档，请先导入资料")
        return {
            "documents": documents,
            "config": self._config_snapshot(),
            "dataset": self._dataset_snapshot(),
        }

    def create_build_job(
        self,
        *,
        idempotency_key: str,
        auto_promote: bool = True,
    ) -> tuple[Job, bool]:
        request = self._build_request()
        identity = {
            "documents": [
                {"id": item["id"], "checksum": item["checksum"]}
                for item in request["documents"]
            ],
            "config": request["config"],
            "dataset": request["dataset"],
        }
        build_key = _canonical_hash(identity)
        physical_collection = (
            f"{self.settings.milvus_collection_prefix}_{build_key[:20]}"
        )
        generation, _ = self.control.get_or_create_generation(
            build_key=build_key,
            physical_collection=physical_collection,
            request=request,
        )
        total = len(request["documents"]) + 4
        return self.control.get_or_create_job(
            generation_id=generation.id,
            kind="build_validate_release",
            idempotency_key=idempotency_key,
            request={"auto_promote": bool(auto_promote), "build_key": build_key},
            progress_total=total,
        )

    def _vectors_for(self, collection_name: str) -> MilvusVectorStore:
        return MilvusVectorStore(
            replace(self.settings, milvus_collection=collection_name),
            self.store.repo_id,
            client=self.store.vectors.client,
        )

    @staticmethod
    def _document_from_snapshot(snapshot: dict[str, Any]) -> DocumentRecord:
        return DocumentRecord(
            id=str(snapshot["id"]),
            name=str(snapshot["name"]),
            stored_path=str(snapshot["stored_path"]),
            content_type=str(
                snapshot.get("content_type") or "application/octet-stream"
            ),
            checksum=str(snapshot["checksum"]),
            file_size=int(snapshot.get("file_size") or 0),
            character_count=int(snapshot.get("character_count") or 0),
            chunk_count=0,
            status="staged",
            error_message=None,
            embedding_provider=None,
            embedding_model=None,
            embedding_dimensions=None,
            created_at="",
            updated_at="",
        )

    def _validate_candidate(
        self,
        generation: Generation,
        target: MilvusVectorStore | None = None,
    ) -> dict[str, Any]:
        candidate = target or self._vectors_for(generation.physical_collection)
        validation = candidate.validate_ready()
        expected = {
            item.chunk_id: (item.document_id, item.content_hash)
            for item in self.control.list_release_chunks(generation.id)
        }
        actual_rows = candidate.scan_rows()
        actual = {
            str(row.get("id") or ""): (
                str(row.get("document_id") or ""),
                str((row.get("metadata") or {}).get("content_hash") or ""),
            )
            for row in actual_rows
        }
        missing = sorted(expected.keys() - actual.keys())
        orphan = sorted(actual.keys() - expected.keys())
        mismatch = sorted(
            chunk_id
            for chunk_id in expected.keys() & actual.keys()
            if expected[chunk_id] != actual[chunk_id]
        )
        return {
            **validation,
            "expected_row_count": len(expected),
            "missing_chunks": missing,
            "orphan_chunks": orphan,
            "hash_mismatches": mismatch,
            "passed": (
                int(validation.get("row_count") or 0) == len(expected)
                and not missing
                and not orphan
                and not mismatch
            ),
        }

    def _reuse_rows(self, generation: Generation) -> dict[str, dict[str, Any]]:
        active = self.control.get_active_generation()
        if active is None:
            return {}
        previous = self.control.get_generation(active.generation_id)
        if previous is None or previous.request.get("config") != generation.request.get(
            "config"
        ):
            return {}
        rows = self._vectors_for(active.physical_collection).scan_rows(
            include_vectors=True
        )
        return {str(row.get("id") or ""): row for row in rows if row.get("id")}

    async def run_build_job(self, job_id: str) -> None:
        async with self._build_lock:
            job = self.control.get_job(job_id)
            if job is None or job.state == "succeeded":
                return
            generation = self.control.get_generation(job.generation_id)
            if generation is None:
                return
            if generation.state in {"active", "archived", "passed"}:
                self.control.update_job(
                    job.id,
                    state="running",
                    progress_current=job.progress_current,
                    message="正在复用相同 build_key 的验收结果",
                )
                try:
                    if bool(job.request.get("auto_promote")):
                        await self.promote(generation.id)
                    self.control.update_job(
                        job.id,
                        state="succeeded",
                        progress_current=job.progress_total or job.progress_current,
                        message="已复用相同 build_key 的验收结果",
                    )
                except Exception as exc:
                    latest_job = self.control.get_job(job.id)
                    if latest_job and latest_job.state == "running":
                        self.control.update_job(
                            job.id,
                            state="failed",
                            progress_current=latest_job.progress_current,
                            message="复用验收结果时发布失败，请执行对账",
                            error_message=str(exc),
                        )
                return
            if generation.state == "failed":
                self.control.update_job(
                    job.id,
                    state="failed",
                    progress_current=job.progress_current,
                    message="相同 build_key 的 Generation 已失败",
                    error_message=generation.error_message or "Generation 构建失败",
                )
                return

            target = self._vectors_for(generation.physical_collection)
            report: dict[str, Any] = {
                "generation_id": generation.id,
                "physical_collection": generation.physical_collection,
                "build": {},
                "validation": {},
                "quality_gate": {},
            }
            try:
                self.control.update_job(
                    job.id,
                    state="running",
                    progress_current=0,
                    message="正在冻结语料快照",
                )
                if target._has_collection():
                    target.reset()
                reusable = await asyncio.to_thread(self._reuse_rows, generation)
                manifest_documents: list[ManifestDocument] = []
                manifest_chunks: list[ManifestChunk] = []
                reused_count = 0
                embedded_count = 0
                source_rows = list(generation.request.get("documents") or [])

                for index, snapshot in enumerate(source_rows, start=1):
                    path = Path(str(snapshot["stored_path"]))
                    data = await asyncio.to_thread(path.read_bytes)
                    checksum = hashlib.sha256(data).hexdigest()
                    if checksum != snapshot["checksum"]:
                        raise ValueError(f"构建期间源文件发生变化：{snapshot['name']}")
                    document = self._document_from_snapshot(snapshot)
                    prepared = await self.pipeline.prepare_document(
                        document,
                        data,
                        reusable_rows=reusable,
                    )
                    await asyncio.to_thread(
                        target.replace_document,
                        document_id=document.id,
                        filename=document.name,
                        title=prepared.title,
                        chunks=prepared.chunks,
                        embedding_provider=self.models.embedding_provider,
                        embedding_model=self.models.embedding_model,
                    )
                    reused_count += prepared.reused_chunk_count
                    embedded_count += prepared.embedded_chunk_count
                    manifest_documents.append(
                        ManifestDocument(document.id, document.checksum)
                    )
                    manifest_chunks.extend(
                        ManifestChunk(
                            chunk_id=chunk.id,
                            document_id=document.id,
                            content_hash=str(chunk.metadata["content_hash"]),
                        )
                        for chunk in prepared.chunks
                    )
                    self.control.update_job(
                        job.id,
                        progress_current=index,
                        message=f"已构建 {index}/{len(source_rows)} 份文档",
                        payload={
                            "document": document.name,
                            "reused_chunks": prepared.reused_chunk_count,
                            "embedded_chunks": prepared.embedded_chunk_count,
                        },
                    )

                self.control.replace_release_manifest(
                    generation.id,
                    documents=manifest_documents,
                    chunks=manifest_chunks,
                )
                report["build"] = {
                    "document_count": len(manifest_documents),
                    "chunk_count": len(manifest_chunks),
                    "reused_chunk_count": reused_count,
                    "embedded_chunk_count": embedded_count,
                }
                progress = len(source_rows) + 1
                self.control.update_job(
                    job.id,
                    progress_current=progress,
                    message="正在校验候选 Collection",
                )
                self.control.transition_generation(generation.id, "validating")
                report["validation"] = await asyncio.to_thread(
                    self._validate_candidate,
                    generation,
                    target,
                )
                if not report["validation"]["passed"]:
                    raise ValueError("候选 Collection 与发布 Manifest 不一致")

                progress += 1
                self.control.update_job(
                    job.id,
                    progress_current=progress,
                    message="正在运行固定评测集",
                )
                snapshot_store = RAGStore(
                    self.settings,
                    self.store.repo_id,
                    vector_store=target,
                    snapshot_mode=True,
                )
                evaluation_pipeline = RAGPipeline(
                    self.settings,
                    snapshot_store,
                    self.models,
                    self.reranker,
                )
                expected_dataset = generation.request.get("dataset")
                if expected_dataset != self._dataset_snapshot():
                    raise ValueError("固定评测集在构建期间发生变化，请重新发起构建")
                active = self.control.get_active_generation()
                baseline = None
                if active:
                    baseline_generation = self.control.get_generation(active.generation_id)
                    baseline = (
                        baseline_generation.report.get("quality_gate")
                        if baseline_generation
                        else None
                    )
                quality = await evaluate_release(
                    settings=self.settings,
                    pipeline=evaluation_pipeline,
                    cases_path=self.eval_cases_path,
                    baseline=baseline,
                )
                if (
                    quality.get("dataset_sha256")
                    and isinstance(expected_dataset, dict)
                    and quality["dataset_sha256"] != expected_dataset.get("sha256")
                ):
                    raise ValueError("固定评测集在质量门禁执行期间发生变化")
                report["quality_gate"] = quality
                self.control.record_generation_report(generation.id, report)
                if not quality["passed"]:
                    raise ValueError("固定评测集未通过发布质量门禁")
                self.control.transition_generation(generation.id, "passed")

                progress += 1
                self.control.update_job(
                    job.id,
                    progress_current=progress,
                    message="候选版本已通过完整性和质量门禁",
                    payload={"quality_gate": quality},
                )
                if bool(job.request.get("auto_promote")):
                    await self.promote(generation.id)
                progress = job.progress_total or (progress + 1)
                self.control.update_job(
                    job.id,
                    state="succeeded",
                    progress_current=progress,
                    message="构建、验收和发布流程完成",
                )
            except Exception as exc:
                current = self.control.get_generation(generation.id)
                report.setdefault("error", str(exc))
                try:
                    self.control.record_generation_report(generation.id, report)
                except Exception:
                    pass
                if current and current.state in {"building", "validating"}:
                    self.control.transition_generation(
                        generation.id,
                        "failed",
                        error_message=str(exc),
                    )
                latest_job = self.control.get_job(job.id)
                if latest_job and latest_job.state != "failed":
                    self.control.update_job(
                        job.id,
                        state="failed",
                        progress_current=latest_job.progress_current,
                        message="构建任务失败，请通过对账接口确认线上发布状态",
                        error_message=str(exc),
                    )

    async def promote(self, generation_id: str) -> dict[str, Any]:
        async with self._release_lock:
            return await self._promote_locked(generation_id)

    async def _promote_locked(self, generation_id: str) -> dict[str, Any]:
        generation = self.control.get_generation(generation_id)
        if generation is None:
            raise ValueError("Generation 不存在")
        if generation.state not in {"passed", "active", "archived"}:
            raise ValueError("只有通过质量门禁的 Generation 才能发布")
        quality = dict(generation.report.get("quality_gate") or {})
        if not quality.get("passed"):
            raise ValueError("Generation 没有通过质量门禁")
        validation = await asyncio.to_thread(
            self._validate_candidate,
            generation,
        )
        if not validation["passed"]:
            raise ValueError("候选 Collection 已漂移，拒绝切换 Alias")
        observed = await asyncio.to_thread(
            self.store.vectors.point_alias,
            self.settings.milvus_collection,
            generation.physical_collection,
        )
        mirror = await asyncio.to_thread(
            self.control.set_active_generation,
            generation.id,
            alias_name=self.settings.milvus_collection,
            observed_collection=observed,
        )
        final_observed = await asyncio.to_thread(
            self.store.vectors.alias_target,
            self.settings.milvus_collection,
        )
        if final_observed != generation.physical_collection:
            actual_generation = (
                self.control.get_generation_by_collection(final_observed)
                if final_observed
                else None
            )
            if actual_generation and actual_generation.state in {
                "passed",
                "archived",
                "active",
            }:
                self.control.set_active_generation(
                    actual_generation.id,
                    alias_name=self.settings.milvus_collection,
                    observed_collection=final_observed,
                )
            raise RuntimeError("Alias 在发布过程中被其他任务改写，请先执行对账")

        manifest_documents = self.control.list_release_documents(generation.id)
        released_document_ids = {item.document_id for item in manifest_documents}
        for item in manifest_documents:
            document = self.store.catalog.get_document(item.document_id)
            if document and document.checksum == item.content_hash:
                self.store.catalog.mark_released(
                    document_id=document.id,
                    chunk_count=item.chunk_count,
                    embedding_provider=self.models.embedding_provider,
                    embedding_model=self.models.embedding_model,
                    embedding_dimensions=self.settings.embedding_dimensions,
                )
        self.store.catalog.mark_unreleased_pending(released_document_ids)
        finalized_deletions = self.store.catalog.finalize_released_deletions(
            released_document_ids
        )
        return {
            **asdict(mirror),
            "validation": validation,
            "finalized_deletions": finalized_deletions,
        }

    async def rollback(self, generation_id: str) -> dict[str, Any]:
        generation = self.control.get_generation(generation_id)
        if generation is None or generation.state not in {"archived", "active"}:
            raise ValueError("只能回滚到仍保留的已发布 Generation")
        result = await self.promote(generation_id)
        return {**result, "rolled_back": True}

    def list_releases(self) -> list[dict[str, Any]]:
        active = self.control.get_active_generation()
        rows: list[dict[str, Any]] = []
        for generation in self.control.list_generations():
            summary = self.control.manifest_summary(generation.id)
            rows.append(
                {
                    **asdict(generation),
                    "manifest": asdict(summary),
                    "is_active": bool(active and active.generation_id == generation.id),
                }
            )
        return rows

    def job_detail(self, job_id: str) -> dict[str, Any] | None:
        job = self.control.get_job(job_id)
        if job is None:
            return None
        return {
            **asdict(job),
            "events": [
                asdict(event)
                for event in self.control.list_events(job_id=job_id, limit=500)
            ],
        }

    async def reconcile(self) -> dict[str, Any]:
        async with self._release_lock:
            return await self._reconcile_locked()

    async def _reconcile_locked(self) -> dict[str, Any]:
        alias_target = await asyncio.to_thread(
            self.store.vectors.alias_target,
            self.settings.milvus_collection,
        )
        mirror = self.control.get_active_generation()
        repaired_mirror = False
        if alias_target and (mirror is None or mirror.physical_collection != alias_target):
            observed_generation = self.control.get_generation_by_collection(alias_target)
            if observed_generation and observed_generation.state in {"passed", "archived", "active"}:
                mirror = self.control.set_active_generation(
                    observed_generation.id,
                    alias_name=self.settings.milvus_collection,
                    observed_collection=alias_target,
                )
                repaired_mirror = True
        if alias_target is None:
            return {
                "clean": mirror is None,
                "alias": self.settings.milvus_collection,
                "alias_target": None,
                "control_generation": asdict(mirror) if mirror else None,
                "mirror_repaired": repaired_mirror,
                "missing_chunks": [],
                "orphan_chunks": [],
                "hash_mismatches": [],
                "detail": (
                    "尚未发布任何 Generation"
                    if mirror is None
                    else "Milvus Alias 不存在，但控制面仍记录 Active Generation"
                ),
            }
        generation = self.control.get_generation_by_collection(alias_target)
        if generation is None:
            return {
                "clean": False,
                "alias": self.settings.milvus_collection,
                "alias_target": alias_target,
                "control_generation": None,
                "mirror_repaired": repaired_mirror,
                "missing_chunks": [],
                "orphan_chunks": [],
                "hash_mismatches": [],
                "detail": "Alias 指向控制面未知的物理 Collection",
            }
        try:
            validation = await asyncio.to_thread(
                self._validate_candidate,
                generation,
            )
        except Exception as exc:
            return {
                "clean": False,
                "alias": self.settings.milvus_collection,
                "alias_target": alias_target,
                "control_generation": generation.id,
                "mirror_repaired": repaired_mirror,
                "missing_chunks": [],
                "orphan_chunks": [],
                "hash_mismatches": [],
                "detail": f"Alias 目标 Collection 无法通过结构校验：{exc}",
            }
        clean = bool(validation["passed"])
        return {
            "clean": clean,
            "alias": self.settings.milvus_collection,
            "alias_target": alias_target,
            "control_generation": generation.id,
            "mirror_repaired": repaired_mirror,
            "manifest": asdict(self.control.manifest_summary(generation.id)),
            "actual_chunk_count": int(validation.get("row_count") or 0),
            "missing_chunks": validation["missing_chunks"],
            "orphan_chunks": validation["orphan_chunks"],
            "hash_mismatches": validation["hash_mismatches"],
            "validation": validation,
            "detail": (
                "Alias、Manifest 与 Milvus 数据一致"
                if clean
                else "发现漂移；请构建新 Generation，通过门禁后再切换 Alias"
            ),
        }

    async def recover_interrupted_jobs(self) -> None:
        for job in self.control.list_jobs(limit=1000):
            if job.state not in {"pending", "running"}:
                continue
            self.control.update_job(
                job.id,
                state="failed",
                progress_current=job.progress_current,
                message="应用重启后结束未完成任务",
                error_message="当前本地后台任务不跨进程恢复，请使用新的幂等键重新构建",
            )

    def status(self) -> dict[str, Any]:
        active = self.control.get_active_generation()
        active_payload = asdict(active) if active else None
        if active and active_payload is not None:
            summary = self.control.manifest_summary(active.generation_id)
            active_payload["document_count"] = summary.document_count
            active_payload["chunk_count"] = summary.chunk_count
        desired = {
            document.id: document.checksum
            for document in self.store.catalog.list_desired_documents()
        }
        published = (
            {
                document.document_id: document.content_hash
                for document in self.control.list_release_documents(
                    active.generation_id
                )
            }
            if active
            else {}
        )
        pending = sum(
            desired.get(document_id) != published.get(document_id)
            for document_id in desired.keys() | published.keys()
        )
        return {
            "active": active_payload,
            "pending_document_count": pending,
            "generation_count": len(self.control.list_generations()),
            "job_count": len(self.control.list_jobs()),
            "quality_dataset": self.eval_cases_path.name,
        }
