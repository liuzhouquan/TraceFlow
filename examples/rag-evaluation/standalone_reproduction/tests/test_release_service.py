from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import release_service as release_module
from config import load_settings
from rag_pipeline import PreparedDocument
from rag_store import ChunkPayload, RAGStore
from release_service import ReleaseService


class FakeVectors:
    def __init__(self) -> None:
        self.client = object()
        self.rows: list[dict[str, Any]] = []
        self.alias: str | None = None
        self.alias_switches: list[tuple[str, str]] = []

    def _has_collection(self) -> bool:
        return bool(self.rows)

    def reset(self) -> None:
        self.rows = []

    def replace_document(
        self,
        *,
        document_id: str,
        chunks: list[ChunkPayload],
        **_: Any,
    ) -> int:
        self.rows = [
            row for row in self.rows if row.get("document_id") != document_id
        ] + [
            {
                "id": chunk.id,
                "document_id": document_id,
                "metadata": chunk.metadata,
                "content": chunk.content,
            }
            for chunk in chunks
        ]
        return 384

    def validate_ready(self) -> dict[str, Any]:
        return {"schema_ready": True, "dimensions": 384, "row_count": len(self.rows)}

    def scan_rows(self, **_: Any) -> list[dict[str, Any]]:
        return list(self.rows)

    def point_alias(self, alias: str, collection_name: str) -> str:
        self.alias = collection_name
        self.alias_switches.append((alias, collection_name))
        return collection_name

    def alias_target(self, _: str | None = None) -> str | None:
        return self.alias

    def status(self) -> dict[str, Any]:
        return {"collection": "active", "alias_target": self.alias}

    def close(self) -> None:
        return None


class DummyModels:
    embedding_provider = "deterministic"
    embedding_model = "deterministic-hash-384"


class DummyPipeline:
    async def prepare_document(self, document: Any, _: bytes, **__: Any) -> PreparedDocument:
        content_hash = hashlib.sha256(document.checksum.encode("utf-8")).hexdigest()
        chunk = ChunkPayload(
            id=f"chunk-{document.id}",
            position=0,
            content="release policy",
            lexical_text="release policy",
            vector=[0.0] * 384,
            metadata={"content_hash": content_hash, "parent_id": "parent"},
        )
        return PreparedDocument(
            title=document.name,
            character_count=document.character_count,
            chunks=[chunk],
            reused_chunk_count=0,
            embedded_chunk_count=1,
        )


def make_service(tmp_path: Path) -> tuple[ReleaseService, FakeVectors]:
    base = load_settings()
    settings = replace(
        base,
        provider="deterministic",
        embedding_dimensions=384,
        embedding_model="deterministic-hash-384",
        catalog_path=tmp_path / "control.sqlite3",
        upload_dir=tmp_path / "uploads",
        milvus_collection="test_active_alias",
        milvus_collection_prefix="test_generation",
    )
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    vectors = FakeVectors()
    store = RAGStore(settings, vector_store=vectors)
    service = ReleaseService(
        settings,
        store,
        DummyModels(),  # type: ignore[arg-type]
        DummyPipeline(),  # type: ignore[arg-type]
    )
    service._vectors_for = lambda _: vectors  # type: ignore[method-assign]
    source = settings.upload_dir / "policy.md"
    data = b"# release policy"
    source.write_bytes(data)
    store.catalog.stage_document(
        name="policy.md",
        stored_path=str(source),
        content_type="text/markdown",
        checksum=hashlib.sha256(data).hexdigest(),
        file_size=len(data),
        character_count=len(data),
    )
    return service, vectors


def test_build_gate_and_alias_promotion_are_one_idempotent_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, vectors = make_service(tmp_path)

    async def passed_gate(**_: Any) -> dict[str, Any]:
        return {"passed": True, "metrics": {"recall_at_k": 1.0}}

    monkeypatch.setattr(release_module, "evaluate_release", passed_gate)
    job, created = service.create_build_job(
        idempotency_key="same-request",
        auto_promote=True,
    )
    asyncio.run(service.run_build_job(job.id))

    completed = service.control.get_job(job.id)
    active = service.control.get_active_generation()
    assert created is True
    assert completed is not None and completed.state == "succeeded"
    assert active is not None
    assert vectors.alias == active.physical_collection
    assert service.control.manifest_summary(active.generation_id).chunk_count == 1

    replay, replay_created = service.create_build_job(
        idempotency_key="same-request",
        auto_promote=True,
    )
    assert replay_created is False
    assert replay.id == job.id
    assert len(service.control.list_generations()) == 1


def test_failed_quality_gate_never_moves_the_active_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, vectors = make_service(tmp_path)

    async def failed_gate(**_: Any) -> dict[str, Any]:
        return {"passed": False, "metrics": {"recall_at_k": 0.0}}

    monkeypatch.setattr(release_module, "evaluate_release", failed_gate)
    job, _ = service.create_build_job(
        idempotency_key="bad-candidate",
        auto_promote=True,
    )
    asyncio.run(service.run_build_job(job.id))

    failed_job = service.control.get_job(job.id)
    generation = service.control.get_generation(job.generation_id)
    assert failed_job is not None and failed_job.state == "failed"
    assert generation is not None and generation.state == "failed"
    assert vectors.alias_switches == []
    assert generation.report["quality_gate"]["passed"] is False


def test_reconcile_detects_manifest_drift_without_patching_active_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, vectors = make_service(tmp_path)

    async def passed_gate(**_: Any) -> dict[str, Any]:
        return {"passed": True, "metrics": {"recall_at_k": 1.0}}

    monkeypatch.setattr(release_module, "evaluate_release", passed_gate)
    job, _ = service.create_build_job(
        idempotency_key="drift-source",
        auto_promote=True,
    )
    asyncio.run(service.run_build_job(job.id))
    vectors.rows.append(
        {
            "id": "orphan-chunk",
            "document_id": "orphan-document",
            "metadata": {"content_hash": "orphan"},
        }
    )

    result = asyncio.run(service.reconcile())

    assert result["clean"] is False
    assert result["orphan_chunks"] == ["orphan-chunk"]
    assert any(row["id"] == "orphan-chunk" for row in vectors.rows)


def test_new_job_for_active_build_key_transitions_through_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = make_service(tmp_path)

    async def passed_gate(**_: Any) -> dict[str, Any]:
        return {"passed": True, "metrics": {"recall_at_k": 1.0}}

    monkeypatch.setattr(release_module, "evaluate_release", passed_gate)
    first, _ = service.create_build_job(
        idempotency_key="initial-build",
        auto_promote=True,
    )
    asyncio.run(service.run_build_job(first.id))

    replay, created = service.create_build_job(
        idempotency_key="new-request-same-build",
        auto_promote=True,
    )
    assert created is True
    assert replay.state == "pending"

    asyncio.run(service.run_build_job(replay.id))

    completed = service.control.get_job(replay.id)
    assert completed is not None and completed.state == "succeeded"
    assert completed.started_at is not None


def test_build_uses_frozen_document_metadata_when_catalog_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, vectors = make_service(tmp_path)

    async def passed_gate(**_: Any) -> dict[str, Any]:
        return {"passed": True, "metrics": {"recall_at_k": 1.0}}

    monkeypatch.setattr(release_module, "evaluate_release", passed_gate)
    original = service.store.catalog.get_by_name("policy.md")
    assert original is not None
    job, _ = service.create_build_job(
        idempotency_key="frozen-source",
        auto_promote=False,
    )

    replacement_data = b"# replacement policy"
    replacement_path = service.settings.upload_dir / "replacement-policy.md"
    replacement_path.write_bytes(replacement_data)
    replacement, changed = service.store.catalog.stage_document(
        name="policy.md",
        stored_path=str(replacement_path),
        content_type="text/markdown",
        checksum=hashlib.sha256(replacement_data).hexdigest(),
        file_size=len(replacement_data),
        character_count=len(replacement_data),
    )
    assert changed is True and replacement.id == original.id

    asyncio.run(service.run_build_job(job.id))

    manifest = service.control.list_release_documents(job.generation_id)
    current = service.store.catalog.get_document(original.id)
    assert manifest[0].content_hash == original.checksum
    assert current is not None and current.status == "staged"
    expected_chunk_hash = hashlib.sha256(original.checksum.encode("utf-8")).hexdigest()
    assert vectors.rows[0]["metadata"]["content_hash"] == expected_chunk_hash


def test_manual_promotion_revalidates_candidate_before_alias_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, vectors = make_service(tmp_path)

    async def passed_gate(**_: Any) -> dict[str, Any]:
        return {"passed": True, "metrics": {"recall_at_k": 1.0}}

    monkeypatch.setattr(release_module, "evaluate_release", passed_gate)
    job, _ = service.create_build_job(
        idempotency_key="drift-before-promote",
        auto_promote=False,
    )
    asyncio.run(service.run_build_job(job.id))
    vectors.rows[0]["metadata"] = {"content_hash": "tampered"}

    with pytest.raises(ValueError, match="漂移"):
        asyncio.run(service.promote(job.generation_id))

    assert vectors.alias_switches == []
    assert service.control.get_active_generation() is None


def test_published_deletion_is_hidden_and_pending_detects_rollback_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = make_service(tmp_path)

    async def passed_gate(**_: Any) -> dict[str, Any]:
        return {"passed": True, "metrics": {"recall_at_k": 1.0}}

    monkeypatch.setattr(release_module, "evaluate_release", passed_gate)
    keep_data = b"# keep"
    keep_path = service.settings.upload_dir / "keep.md"
    keep_path.write_bytes(keep_data)
    service.store.catalog.stage_document(
        name="keep.md",
        stored_path=str(keep_path),
        content_type="text/markdown",
        checksum=hashlib.sha256(keep_data).hexdigest(),
        file_size=len(keep_data),
        character_count=len(keep_data),
    )
    deleted = service.store.catalog.get_by_name("policy.md")
    assert deleted is not None

    first, _ = service.create_build_job(
        idempotency_key="before-delete",
        auto_promote=True,
    )
    asyncio.run(service.run_build_job(first.id))
    service.store.catalog.stage_delete(deleted.id)
    assert service.status()["pending_document_count"] == 1

    second, _ = service.create_build_job(
        idempotency_key="publish-delete",
        auto_promote=True,
    )
    asyncio.run(service.run_build_job(second.id))

    tombstone = service.store.catalog.get_document(deleted.id)
    assert tombstone is not None and tombstone.status == "removed"
    assert deleted.id not in {item.id for item in service.store.list_documents()}
    assert service.status()["pending_document_count"] == 0

    first_generation = service.control.get_generation(first.generation_id)
    assert first_generation is not None and first_generation.state == "archived"
    service.control.set_active_generation(
        first_generation.id,
        alias_name=service.settings.milvus_collection,
        observed_collection=first_generation.physical_collection,
    )
    assert service.status()["pending_document_count"] == 1


def test_unreleased_documents_return_to_staged_state_after_snapshot_switch(
    tmp_path: Path,
) -> None:
    service, _ = make_service(tmp_path)
    original = service.store.catalog.get_by_name("policy.md")
    assert original is not None
    service.store.catalog.mark_released(
        document_id=original.id,
        chunk_count=1,
        embedding_provider="deterministic",
        embedding_model="deterministic-hash-384",
        embedding_dimensions=384,
    )

    extra_data = b"# extra policy"
    extra_path = service.settings.upload_dir / "extra.md"
    extra_path.write_bytes(extra_data)
    extra, _ = service.store.catalog.stage_document(
        name="extra.md",
        stored_path=str(extra_path),
        content_type="text/markdown",
        checksum=hashlib.sha256(extra_data).hexdigest(),
        file_size=len(extra_data),
        character_count=len(extra_data),
    )
    service.store.catalog.mark_released(
        document_id=extra.id,
        chunk_count=1,
        embedding_provider="deterministic",
        embedding_model="deterministic-hash-384",
        embedding_dimensions=384,
    )

    changed = service.store.catalog.mark_unreleased_pending({original.id})
    pending = service.store.catalog.get_document(extra.id)

    assert changed == 1
    assert pending is not None
    assert pending.status == "staged"
    assert pending.chunk_count == 0


def test_dataset_contents_are_part_of_build_identity(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    dataset = tmp_path / "held_out.jsonl"
    dataset.write_text('{"id":"one","query":"first"}\n', encoding="utf-8")
    service.eval_cases_path = dataset

    first, _ = service.create_build_job(
        idempotency_key="dataset-v1",
        auto_promote=False,
    )
    dataset.write_text('{"id":"one","query":"second"}\n', encoding="utf-8")
    second, _ = service.create_build_job(
        idempotency_key="dataset-v2",
        auto_promote=False,
    )

    assert first.generation_id != second.generation_id
    first_generation = service.control.get_generation(first.generation_id)
    second_generation = service.control.get_generation(second.generation_id)
    assert first_generation is not None and second_generation is not None
    assert (
        first_generation.request["dataset"]["sha256"]
        != second_generation.request["dataset"]["sha256"]
    )
