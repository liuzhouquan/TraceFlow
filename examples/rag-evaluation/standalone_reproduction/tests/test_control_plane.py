from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from control_plane import (
    ControlPlane,
    ControlPlaneError,
    IdempotencyConflict,
    InvalidStateTransition,
    ManifestChunk,
    ManifestDocument,
    ManifestValidationError,
)


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "control" / "release.sqlite3"


@pytest.fixture
def control_plane(database_path: Path) -> ControlPlane:
    return ControlPlane(database_path)


def create_generation(
    control_plane: ControlPlane,
    suffix: str = "001",
):
    generation, created = control_plane.get_or_create_generation(
        build_key=f"build-{suffix}",
        physical_collection=f"rag_studio_release_{suffix}",
        request={
            "snapshot": f"snapshot-{suffix}",
            "embedding": {"model": "qwen3-embedding:0.6b", "dimensions": 1024},
            "chunking_version": "v3",
        },
    )
    assert created is True
    return generation


def pass_generation(control_plane: ControlPlane, generation_id: str) -> None:
    control_plane.transition_generation(generation_id, "validating")
    control_plane.transition_generation(generation_id, "passed")


def test_build_key_is_canonical_and_idempotent(control_plane: ControlPlane) -> None:
    request = {
        "embedding": {"dimensions": 1024, "model": "qwen3-embedding:0.6b"},
        "snapshot": "snapshot-a",
    }
    first, created = control_plane.get_or_create_generation(
        build_key="snapshot-a:v3:qwen",
        physical_collection="rag_release_a",
        request=request,
    )
    assert created is True
    assert first.state == "building"

    repeated, created = control_plane.get_or_create_generation(
        build_key="snapshot-a:v3:qwen",
        physical_collection="rag_release_a",
        request={"snapshot": "snapshot-a", "embedding": request["embedding"]},
    )
    assert created is False
    assert repeated.id == first.id
    assert repeated.request_fingerprint == first.request_fingerprint

    with pytest.raises(IdempotencyConflict, match="不同的构建请求"):
        control_plane.get_or_create_generation(
            build_key="snapshot-a:v3:qwen",
            physical_collection="rag_release_a",
            request={"snapshot": "snapshot-b", "embedding": request["embedding"]},
        )
    with pytest.raises(IdempotencyConflict, match="不同的构建请求"):
        control_plane.get_or_create_generation(
            build_key="snapshot-a:v3:qwen",
            physical_collection="rag_release_other",
            request=request,
        )


def test_concurrent_build_requests_return_one_generation(database_path: Path) -> None:
    control_plane = ControlPlane(database_path)

    def request_generation(_: int) -> tuple[str, bool]:
        generation, created = control_plane.get_or_create_generation(
            build_key="one-snapshot",
            physical_collection="rag_release_one",
            request={"snapshot": "abc123", "chunking_version": "v3"},
        )
        return generation.id, created

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(request_generation, range(8)))

    assert len({generation_id for generation_id, _ in results}) == 1
    assert sum(created for _, created in results) == 1


def test_generation_state_machine_rejects_skips_and_records_events(
    control_plane: ControlPlane,
) -> None:
    generation = create_generation(control_plane)
    with pytest.raises(InvalidStateTransition, match="building.*passed"):
        control_plane.transition_generation(generation.id, "passed")
    with pytest.raises(InvalidStateTransition, match="set_active_generation"):
        control_plane.transition_generation(generation.id, "active")

    validating = control_plane.transition_generation(generation.id, "validating")
    assert validating.state == "validating"
    passed = control_plane.transition_generation(generation.id, "passed")
    assert passed.state == "passed"
    assert control_plane.transition_generation(generation.id, "passed") == passed

    event_types = [
        event.event_type
        for event in control_plane.list_events(generation_id=generation.id)
    ]
    assert event_types == ["generation.validating", "generation.passed"]

    failed = create_generation(control_plane, "failed")
    with pytest.raises(ValueError, match="error_message"):
        control_plane.transition_generation(failed.id, "failed")
    failed = control_plane.transition_generation(
        failed.id,
        "failed",
        error_message="Milvus insert timeout",
    )
    assert failed.state == "failed"
    assert failed.error_message == "Milvus insert timeout"
    with pytest.raises(InvalidStateTransition):
        control_plane.transition_generation(failed.id, "validating")


def test_generation_report_roundtrip_and_failed_diagnostics(
    control_plane: ControlPlane,
) -> None:
    generation = create_generation(control_plane)
    report = {
        "manifest": {"clean": True, "documents": 4, "chunks": 18},
        "quality_gate": {
            "passed": True,
            "recall_at_k": 1.0,
            "negative_rejection_rate": 1.0,
        },
    }
    recorded = control_plane.record_generation_report(generation.id, report)
    assert recorded.report == report
    assert control_plane.get_generation(generation.id).report == report

    control_plane.transition_generation(
        generation.id,
        "failed",
        error_message="candidate verification failed",
    )
    diagnostics = {
        **report,
        "failure": {
            "phase": "verify",
            "missing_chunks": ["chunk-18"],
        },
    }
    failed = control_plane.record_generation_report(generation.id, diagnostics)
    assert failed.state == "failed"
    assert failed.report == diagnostics
    assert [
        event.event_type
        for event in control_plane.list_events(generation_id=generation.id)
    ] == [
        "generation.report_recorded",
        "generation.failed",
        "generation.report_recorded",
    ]


def test_job_idempotency_progress_retry_and_event_cursor(
    control_plane: ControlPlane,
) -> None:
    generation = create_generation(control_plane)
    job, created = control_plane.get_or_create_job(
        generation_id=generation.id,
        kind="build",
        idempotency_key="job-build-001",
        request={"batch_size": 64},
        progress_total=10,
    )
    assert created is True
    assert job.state == "pending"
    assert job.progress_current == 0

    repeated, created = control_plane.get_or_create_job(
        generation_id=generation.id,
        kind="build",
        idempotency_key="job-build-001",
        request={"batch_size": 64},
        progress_total=10,
    )
    assert created is False
    assert repeated.id == job.id

    with pytest.raises(IdempotencyConflict, match="不同的 Job 请求"):
        control_plane.get_or_create_job(
            generation_id=generation.id,
            kind="build",
            idempotency_key="job-build-001",
            request={"batch_size": 32},
            progress_total=10,
        )
    with pytest.raises(IdempotencyConflict, match="不同的 Job 请求"):
        control_plane.get_or_create_job(
            generation_id=generation.id,
            kind="build",
            idempotency_key="job-build-001",
            request={"batch_size": 64},
            progress_total=11,
        )

    running = control_plane.update_job(
        job.id,
        state="running",
        progress_current=2,
        message="已写入第一批",
        payload={"batch": 1},
    )
    assert running.started_at is not None
    assert running.progress_current == 2
    assert running.progress_message == "已写入第一批"

    with pytest.raises(ValueError, match="不能倒退"):
        control_plane.update_job(job.id, progress_current=1)
    with pytest.raises(ValueError, match="不能大于"):
        control_plane.update_job(job.id, progress_current=11)

    failed = control_plane.update_job(
        job.id,
        state="failed",
        progress_current=4,
        error_message="network interrupted",
    )
    assert failed.finished_at is not None
    assert failed.error_message == "network interrupted"

    retried = control_plane.update_job(
        job.id,
        state="running",
        progress_current=4,
        message="从第 4 批继续",
    )
    assert retried.finished_at is None
    assert retried.error_message is None
    completed = control_plane.update_job(
        job.id,
        state="succeeded",
        progress_current=10,
        message="构建完成",
    )
    assert completed.finished_at is not None

    events = control_plane.list_events(job_id=job.id)
    assert [event.event_type for event in events] == [
        "job.pending",
        "job.running",
        "job.failed",
        "job.running",
        "job.succeeded",
    ]
    assert events[1].payload == {"batch": 1}
    assert [event.id for event in control_plane.list_events(after_id=events[2].id)] == [
        events[3].id,
        events[4].id,
    ]
    assert control_plane.list_jobs(generation_id=generation.id) == [completed]


def test_manifest_is_transactional_queryable_and_frozen_before_validation(
    control_plane: ControlPlane,
) -> None:
    generation = create_generation(control_plane)
    summary = control_plane.replace_release_manifest(
        generation.id,
        documents=[
            ManifestDocument("doc-a", "doc-hash-a"),
            ManifestDocument("doc-b", "doc-hash-b"),
        ],
        chunks=[
            ManifestChunk("chunk-a-1", "doc-a", "chunk-hash-a-1"),
            ManifestChunk("chunk-a-2", "doc-a", "chunk-hash-a-2"),
            ManifestChunk("chunk-b-1", "doc-b", "chunk-hash-b-1"),
        ],
    )
    assert summary.document_count == 2
    assert summary.chunk_count == 3
    assert control_plane.manifest_summary(generation.id) == summary

    documents = control_plane.list_release_documents(generation.id)
    assert [(item.document_id, item.chunk_count) for item in documents] == [
        ("doc-a", 2),
        ("doc-b", 1),
    ]
    assert [
        item.chunk_id
        for item in control_plane.list_release_chunks(
            generation.id,
            document_id="doc-a",
        )
    ] == ["chunk-a-1", "chunk-a-2"]
    assert (
        control_plane.get_release_chunk(generation.id, "chunk-b-1").content_hash
        == "chunk-hash-b-1"
    )

    with pytest.raises(ManifestValidationError, match="清单外文档"):
        control_plane.replace_release_manifest(
            generation.id,
            documents=[ManifestDocument("doc-new", "hash-new")],
            chunks=[ManifestChunk("orphan", "missing-doc", "hash")],
        )
    assert control_plane.manifest_summary(generation.id) == summary

    control_plane.transition_generation(generation.id, "validating")
    with pytest.raises(InvalidStateTransition, match="building"):
        control_plane.replace_release_manifest(
            generation.id,
            documents=[],
            chunks=[],
        )
    assert control_plane.manifest_summary(generation.id) == summary


def test_active_generation_switch_and_rollback_are_atomic(
    control_plane: ControlPlane,
) -> None:
    first = create_generation(control_plane, "001")
    second = create_generation(control_plane, "002")
    pass_generation(control_plane, first.id)
    pass_generation(control_plane, second.id)

    first_active = control_plane.set_active_generation(
        first.id,
        alias_name="rag_studio_active",
        observed_collection=first.physical_collection,
    )
    assert control_plane.get_active_generation() == first_active
    assert control_plane.get_generation(first.id).state == "active"

    second_active = control_plane.set_active_generation(
        second.id,
        alias_name="rag_studio_active",
    )
    assert control_plane.get_active_generation() == second_active
    assert control_plane.get_generation(first.id).state == "archived"
    assert control_plane.get_generation(second.id).state == "active"

    rolled_back = control_plane.set_active_generation(
        first.id,
        alias_name="rag_studio_active",
    )
    assert control_plane.get_active_generation() == rolled_back
    assert control_plane.get_generation(first.id).state == "active"
    assert control_plane.get_generation(second.id).state == "archived"

    with pytest.raises(ControlPlaneError, match="Alias 指向"):
        control_plane.set_active_generation(
            first.id,
            alias_name="rag_studio_active",
            observed_collection="wrong_collection",
        )

    control_plane.clear_active_generation(alias_name="rag_studio_active")
    assert control_plane.get_active_generation() is None
    assert control_plane.get_generation(first.id).state == "archived"


def test_sqlite_is_control_metadata_only_and_enforces_foreign_keys(
    control_plane: ControlPlane,
    database_path: Path,
) -> None:
    generation = create_generation(control_plane)
    control_plane.replace_release_manifest(
        generation.id,
        documents=[ManifestDocument("doc-a", "doc-hash")],
        chunks=[ManifestChunk("chunk-a", "doc-a", "chunk-hash")],
    )

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        schema_rows = connection.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY name"
        ).fetchall()
        schema_sql = "\n".join(str(row["sql"] or "") for row in schema_rows).lower()
        assert "virtual table" not in schema_sql
        assert "fts3" not in schema_sql
        assert "fts4" not in schema_sql
        assert "fts5" not in schema_sql

        tables = {
            str(row["name"])
            for row in schema_rows
            if row["type"] == "table" and not str(row["name"]).startswith("sqlite_")
        }
        assert tables == {
            "active_generation_mirror",
            "control_events",
            "generations",
            "jobs",
            "release_chunks",
            "release_documents",
        }
        forbidden_columns = {
            "content",
            "body",
            "text",
            "embedding",
            "vector",
            "sparse_vector",
            "dense_vector",
            "bm25_sparse",
        }
        actual_columns = {
            str(row["name"]).lower()
            for table in tables
            for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        }
        assert actual_columns.isdisjoint(forbidden_columns)

        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO release_documents (
                    generation_id, document_id, content_hash, chunk_count
                ) VALUES ('missing-generation', 'doc-x', 'hash', 0)
                """
            )
    finally:
        connection.close()


def test_existing_control_database_adds_generation_report_column(
    database_path: Path,
) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE generations (
                id TEXT PRIMARY KEY,
                build_key TEXT NOT NULL UNIQUE,
                physical_collection TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK (
                    state IN ('building', 'validating', 'passed', 'failed', 'active', 'archived')
                ),
                request_json TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO generations (
                id, build_key, physical_collection, state,
                request_json, request_fingerprint, created_at, updated_at
            ) VALUES (
                'legacy-generation', 'legacy-build', 'legacy_collection', 'building',
                '{}', 'legacy-fingerprint', '2026-07-26T00:00:00Z', '2026-07-26T00:00:00Z'
            );
            PRAGMA user_version = 1;
            """
        )
    finally:
        connection.close()

    control_plane = ControlPlane(database_path)
    migrated = control_plane.get_generation("legacy-generation")
    assert migrated is not None
    assert migrated.report == {}
    assert control_plane.record_generation_report(
        migrated.id,
        {"validation": {"passed": True}},
    ).report == {"validation": {"passed": True}}

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        report_column = next(
            row
            for row in connection.execute("PRAGMA table_info(generations)").fetchall()
            if row["name"] == "report_json"
        )
        assert report_column["notnull"] == 1
        assert report_column["dflt_value"] == "'{}'"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        connection.close()
