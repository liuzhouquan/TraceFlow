from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


GENERATION_STATES = frozenset(
    {"building", "validating", "passed", "failed", "active", "archived"}
)
JOB_STATES = frozenset({"pending", "running", "succeeded", "failed"})

_GENERATION_TRANSITIONS = {
    "building": frozenset({"validating", "failed"}),
    "validating": frozenset({"passed", "failed"}),
    "passed": frozenset({"failed", "archived"}),
    "failed": frozenset(),
    "active": frozenset(),
    "archived": frozenset(),
}
_JOB_TRANSITIONS = {
    "pending": frozenset({"running", "failed"}),
    "running": frozenset({"succeeded", "failed"}),
    "succeeded": frozenset(),
    "failed": frozenset({"running"}),
}


class ControlPlaneError(RuntimeError):
    pass


class RecordNotFound(ControlPlaneError):
    pass


class IdempotencyConflict(ControlPlaneError):
    pass


class InvalidStateTransition(ControlPlaneError):
    pass


class ManifestValidationError(ControlPlaneError):
    pass


@dataclass(frozen=True)
class Generation:
    id: str
    build_key: str
    physical_collection: str
    state: str
    request: dict[str, Any]
    request_fingerprint: str
    report: dict[str, Any]
    error_message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Job:
    id: str
    generation_id: str
    kind: str
    idempotency_key: str
    state: str
    request: dict[str, Any]
    request_fingerprint: str
    progress_current: int
    progress_total: int | None
    progress_message: str | None
    error_message: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True)
class ControlEvent:
    id: int
    generation_id: str
    job_id: str | None
    event_type: str
    message: str
    payload: dict[str, Any]
    progress_current: int | None
    progress_total: int | None
    created_at: str


@dataclass(frozen=True)
class ManifestDocument:
    document_id: str
    content_hash: str


@dataclass(frozen=True)
class ManifestChunk:
    chunk_id: str
    document_id: str
    content_hash: str


@dataclass(frozen=True)
class ReleaseDocument:
    generation_id: str
    document_id: str
    content_hash: str
    chunk_count: int


@dataclass(frozen=True)
class ReleaseChunk:
    generation_id: str
    chunk_id: str
    document_id: str
    content_hash: str


@dataclass(frozen=True)
class ManifestSummary:
    generation_id: str
    document_count: int
    chunk_count: int


@dataclass(frozen=True)
class ActiveGenerationMirror:
    generation_id: str
    alias_name: str
    physical_collection: str
    observed_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _required(value: str, name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{name} 不能为空")
    return clean


def _canonical_json(value: Mapping[str, Any] | None) -> str:
    try:
        return json.dumps(
            dict(value or {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("控制面请求必须是可序列化的 JSON 对象") from exc


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ControlPlane:
    """SQLite control plane; document bodies and retrieval data stay in Milvus."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._transaction(immediate=True) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS generations (
                    id TEXT PRIMARY KEY,
                    build_key TEXT NOT NULL UNIQUE,
                    physical_collection TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL CHECK (
                        state IN ('building', 'validating', 'passed', 'failed', 'active', 'archived')
                    ),
                    request_json TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    report_json TEXT NOT NULL DEFAULT '{}',
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS one_active_generation
                ON generations(state) WHERE state = 'active';

                CREATE INDEX IF NOT EXISTS generations_state_created
                ON generations(state, created_at DESC);

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    generation_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL CHECK (
                        state IN ('pending', 'running', 'succeeded', 'failed')
                    ),
                    request_json TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    progress_current INTEGER NOT NULL DEFAULT 0 CHECK (progress_current >= 0),
                    progress_total INTEGER CHECK (progress_total IS NULL OR progress_total >= 0),
                    progress_message TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS jobs_generation_created
                ON jobs(generation_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS release_documents (
                    generation_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL CHECK (chunk_count >= 0),
                    PRIMARY KEY (generation_id, document_id),
                    FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS release_chunks (
                    generation_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    PRIMARY KEY (generation_id, chunk_id),
                    FOREIGN KEY (generation_id, document_id)
                        REFERENCES release_documents(generation_id, document_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS release_chunks_document
                ON release_chunks(generation_id, document_id);

                CREATE TABLE IF NOT EXISTS active_generation_mirror (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    generation_id TEXT NOT NULL UNIQUE,
                    alias_name TEXT NOT NULL,
                    physical_collection TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS control_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generation_id TEXT NOT NULL,
                    job_id TEXT,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    progress_current INTEGER CHECK (progress_current IS NULL OR progress_current >= 0),
                    progress_total INTEGER CHECK (progress_total IS NULL OR progress_total >= 0),
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS events_generation_id
                ON control_events(generation_id, id);

                CREATE INDEX IF NOT EXISTS events_job_id
                ON control_events(job_id, id);

                """
            )
            generation_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(generations)").fetchall()
            }
            if "report_json" not in generation_columns:
                connection.execute(
                    """
                    ALTER TABLE generations
                    ADD COLUMN report_json TEXT NOT NULL DEFAULT '{}'
                    """
                )
            connection.execute("PRAGMA user_version = 2")

    @staticmethod
    def _generation(row: sqlite3.Row) -> Generation:
        return Generation(
            id=str(row["id"]),
            build_key=str(row["build_key"]),
            physical_collection=str(row["physical_collection"]),
            state=str(row["state"]),
            request=dict(json.loads(row["request_json"])),
            request_fingerprint=str(row["request_fingerprint"]),
            report=dict(json.loads(row["report_json"])),
            error_message=row["error_message"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        return Job(
            id=str(row["id"]),
            generation_id=str(row["generation_id"]),
            kind=str(row["kind"]),
            idempotency_key=str(row["idempotency_key"]),
            state=str(row["state"]),
            request=dict(json.loads(row["request_json"])),
            request_fingerprint=str(row["request_fingerprint"]),
            progress_current=int(row["progress_current"]),
            progress_total=(
                int(row["progress_total"])
                if row["progress_total"] is not None
                else None
            ),
            progress_message=row["progress_message"],
            error_message=row["error_message"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    @staticmethod
    def _event(row: sqlite3.Row) -> ControlEvent:
        return ControlEvent(
            id=int(row["id"]),
            generation_id=str(row["generation_id"]),
            job_id=row["job_id"],
            event_type=str(row["event_type"]),
            message=str(row["message"]),
            payload=dict(json.loads(row["payload_json"])),
            progress_current=(
                int(row["progress_current"])
                if row["progress_current"] is not None
                else None
            ),
            progress_total=(
                int(row["progress_total"])
                if row["progress_total"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
        )

    def get_or_create_generation(
        self,
        *,
        build_key: str,
        physical_collection: str,
        request: Mapping[str, Any] | None = None,
        generation_id: str | None = None,
    ) -> tuple[Generation, bool]:
        build_key = _required(build_key, "build_key")
        physical_collection = _required(physical_collection, "physical_collection")
        request_json = _canonical_json(request)
        request_fingerprint = _fingerprint(request_json)

        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM generations WHERE build_key = ?",
                (build_key,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["physical_collection"] != physical_collection
                    or existing["request_fingerprint"] != request_fingerprint
                ):
                    raise IdempotencyConflict(
                        f"build_key {build_key!r} 已用于不同的构建请求"
                    )
                return self._generation(existing), False

            timestamp = _now()
            identifier = _required(generation_id or str(uuid.uuid4()), "generation_id")
            try:
                connection.execute(
                    """
                    INSERT INTO generations (
                        id, build_key, physical_collection, state,
                        request_json, request_fingerprint, created_at, updated_at
                    ) VALUES (?, ?, ?, 'building', ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        build_key,
                        physical_collection,
                        request_json,
                        request_fingerprint,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ControlPlaneError(
                    "generation_id 或 physical_collection 已被其他构建占用"
                ) from exc
            row = connection.execute(
                "SELECT * FROM generations WHERE id = ?",
                (identifier,),
            ).fetchone()
            assert row is not None
            return self._generation(row), True

    def get_generation(self, generation_id: str) -> Generation | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM generations WHERE id = ?",
                (generation_id,),
            ).fetchone()
        return self._generation(row) if row is not None else None

    def get_generation_by_build_key(self, build_key: str) -> Generation | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM generations WHERE build_key = ?",
                (build_key,),
            ).fetchone()
        return self._generation(row) if row is not None else None

    def get_generation_by_collection(self, physical_collection: str) -> Generation | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM generations WHERE physical_collection = ?",
                (physical_collection,),
            ).fetchone()
        return self._generation(row) if row is not None else None

    def list_generations(
        self,
        *,
        state: str | None = None,
        limit: int = 100,
    ) -> list[Generation]:
        if state is not None and state not in GENERATION_STATES:
            raise ValueError(f"未知 generation 状态：{state}")
        limit = max(1, min(int(limit), 1000))
        sql = "SELECT * FROM generations"
        parameters: list[Any] = []
        if state is not None:
            sql += " WHERE state = ?"
            parameters.append(state)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._generation(row) for row in rows]

    def transition_generation(
        self,
        generation_id: str,
        new_state: str,
        *,
        error_message: str | None = None,
    ) -> Generation:
        if new_state not in GENERATION_STATES:
            raise ValueError(f"未知 generation 状态：{new_state}")
        if new_state == "active":
            raise InvalidStateTransition("请使用 set_active_generation 原子更新发布镜像")

        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM generations WHERE id = ?",
                (generation_id,),
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"generation {generation_id!r} 不存在")
            current_state = str(row["state"])
            if current_state == new_state:
                return self._generation(row)
            if new_state not in _GENERATION_TRANSITIONS[current_state]:
                raise InvalidStateTransition(
                    f"generation 不能从 {current_state} 变为 {new_state}"
                )
            if new_state == "failed" and not (error_message or "").strip():
                raise ValueError("generation 失败时必须记录 error_message")
            timestamp = _now()
            connection.execute(
                """
                UPDATE generations
                SET state = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    new_state,
                    (error_message or "").strip() or None,
                    timestamp,
                    generation_id,
                ),
            )
            self._insert_event(
                connection,
                generation_id=generation_id,
                job_id=None,
                event_type=f"generation.{new_state}",
                message=f"Generation 状态变为 {new_state}",
                payload_json="{}",
                progress_current=None,
                progress_total=None,
                created_at=timestamp,
            )
            updated = connection.execute(
                "SELECT * FROM generations WHERE id = ?",
                (generation_id,),
            ).fetchone()
            assert updated is not None
            return self._generation(updated)

    def record_generation_report(
        self,
        generation_id: str,
        report: Mapping[str, Any],
    ) -> Generation:
        report_json = _canonical_json(report)
        with self._transaction(immediate=True) as connection:
            generation = connection.execute(
                "SELECT id FROM generations WHERE id = ?",
                (generation_id,),
            ).fetchone()
            if generation is None:
                raise RecordNotFound(f"generation {generation_id!r} 不存在")
            timestamp = _now()
            connection.execute(
                """
                UPDATE generations
                SET report_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (report_json, timestamp, generation_id),
            )
            self._insert_event(
                connection,
                generation_id=generation_id,
                job_id=None,
                event_type="generation.report_recorded",
                message="Generation 验证与质量报告已更新",
                payload_json=_canonical_json(
                    {"report_keys": sorted(str(key) for key in report)}
                ),
                progress_current=None,
                progress_total=None,
                created_at=timestamp,
            )
            updated = connection.execute(
                "SELECT * FROM generations WHERE id = ?",
                (generation_id,),
            ).fetchone()
            assert updated is not None
            return self._generation(updated)

    def get_or_create_job(
        self,
        *,
        generation_id: str,
        kind: str,
        idempotency_key: str,
        request: Mapping[str, Any] | None = None,
        progress_total: int | None = None,
        job_id: str | None = None,
    ) -> tuple[Job, bool]:
        kind = _required(kind, "job kind")
        idempotency_key = _required(idempotency_key, "idempotency_key")
        if progress_total is not None and progress_total < 0:
            raise ValueError("progress_total 不能小于 0")
        request_json = _canonical_json(request)
        identity_json = _canonical_json(
            {
                "generation_id": generation_id,
                "kind": kind,
                "request": json.loads(request_json),
                "progress_total": progress_total,
            }
        )
        request_fingerprint = _fingerprint(identity_json)

        with self._transaction(immediate=True) as connection:
            generation = connection.execute(
                "SELECT id FROM generations WHERE id = ?",
                (generation_id,),
            ).fetchone()
            if generation is None:
                raise RecordNotFound(f"generation {generation_id!r} 不存在")
            existing = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["request_fingerprint"] != request_fingerprint:
                    raise IdempotencyConflict(
                        f"idempotency_key {idempotency_key!r} 已用于不同的 Job 请求"
                    )
                return self._job(existing), False

            identifier = _required(job_id or str(uuid.uuid4()), "job_id")
            timestamp = _now()
            try:
                connection.execute(
                    """
                    INSERT INTO jobs (
                        id, generation_id, kind, idempotency_key, state,
                        request_json, request_fingerprint, progress_total,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        generation_id,
                        kind,
                        idempotency_key,
                        request_json,
                        request_fingerprint,
                        progress_total,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ControlPlaneError("job_id 已被其他任务占用") from exc
            self._insert_event(
                connection,
                generation_id=generation_id,
                job_id=identifier,
                event_type="job.pending",
                message=f"{kind} Job 已创建",
                payload_json="{}",
                progress_current=0,
                progress_total=progress_total,
                created_at=timestamp,
            )
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (identifier,),
            ).fetchone()
            assert row is not None
            return self._job(row), True

    def get_job(self, job_id: str) -> Job | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return self._job(row) if row is not None else None

    def get_job_by_idempotency_key(self, idempotency_key: str) -> Job | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return self._job(row) if row is not None else None

    def list_jobs(
        self,
        *,
        generation_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[Job]:
        if state is not None and state not in JOB_STATES:
            raise ValueError(f"未知 job 状态：{state}")
        clauses: list[str] = []
        parameters: list[Any] = []
        if generation_id is not None:
            clauses.append("generation_id = ?")
            parameters.append(generation_id)
        if state is not None:
            clauses.append("state = ?")
            parameters.append(state)
        sql = "SELECT * FROM jobs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        parameters.append(max(1, min(int(limit), 1000)))
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._job(row) for row in rows]

    def update_job(
        self,
        job_id: str,
        *,
        state: str | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
        message: str | None = None,
        error_message: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> Job:
        if state is not None and state not in JOB_STATES:
            raise ValueError(f"未知 job 状态：{state}")
        payload_json = _canonical_json(payload)

        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise RecordNotFound(f"job {job_id!r} 不存在")
            current = self._job(row)
            new_state = state or current.state
            if new_state != current.state and new_state not in _JOB_TRANSITIONS[current.state]:
                raise InvalidStateTransition(
                    f"job 不能从 {current.state} 变为 {new_state}"
                )
            if new_state == "failed" and not (error_message or current.error_message or "").strip():
                raise ValueError("job 失败时必须记录 error_message")

            next_current = (
                current.progress_current
                if progress_current is None
                else int(progress_current)
            )
            next_total = current.progress_total if progress_total is None else int(progress_total)
            if next_current < current.progress_current:
                raise ValueError("job progress_current 不能倒退")
            if next_current < 0 or (next_total is not None and next_total < 0):
                raise ValueError("job 进度不能小于 0")
            if next_total is not None and next_current > next_total:
                raise ValueError("progress_current 不能大于 progress_total")

            timestamp = _now()
            started_at = current.started_at
            if new_state == "running" and started_at is None:
                started_at = timestamp
            finished_at = (
                timestamp if new_state in {"succeeded", "failed"} else None
            )
            next_error = (
                (error_message or "").strip() or None
                if new_state == "failed"
                else None
            )
            next_message = (
                message.strip() if message is not None and message.strip() else current.progress_message
            )
            connection.execute(
                """
                UPDATE jobs
                SET state = ?, progress_current = ?, progress_total = ?,
                    progress_message = ?, error_message = ?, updated_at = ?,
                    started_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    new_state,
                    next_current,
                    next_total,
                    next_message,
                    next_error,
                    timestamp,
                    started_at,
                    finished_at,
                    job_id,
                ),
            )
            event_type = (
                f"job.{new_state}"
                if new_state != current.state
                else "job.progress"
            )
            self._insert_event(
                connection,
                generation_id=current.generation_id,
                job_id=job_id,
                event_type=event_type,
                message=message or f"Job 状态为 {new_state}",
                payload_json=payload_json,
                progress_current=next_current,
                progress_total=next_total,
                created_at=timestamp,
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            assert updated is not None
            return self._job(updated)

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        generation_id: str,
        job_id: str | None,
        event_type: str,
        message: str,
        payload_json: str,
        progress_current: int | None,
        progress_total: int | None,
        created_at: str,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO control_events (
                generation_id, job_id, event_type, message, payload_json,
                progress_current, progress_total, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generation_id,
                job_id,
                event_type,
                message,
                payload_json,
                progress_current,
                progress_total,
                created_at,
            ),
        )
        return int(cursor.lastrowid)

    def append_event(
        self,
        *,
        generation_id: str,
        event_type: str,
        message: str,
        job_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
    ) -> ControlEvent:
        event_type = _required(event_type, "event_type")
        message = _required(message, "event message")
        if progress_current is not None and progress_current < 0:
            raise ValueError("progress_current 不能小于 0")
        if progress_total is not None and progress_total < 0:
            raise ValueError("progress_total 不能小于 0")
        if (
            progress_current is not None
            and progress_total is not None
            and progress_current > progress_total
        ):
            raise ValueError("progress_current 不能大于 progress_total")
        payload_json = _canonical_json(payload)

        with self._transaction(immediate=True) as connection:
            generation = connection.execute(
                "SELECT id FROM generations WHERE id = ?",
                (generation_id,),
            ).fetchone()
            if generation is None:
                raise RecordNotFound(f"generation {generation_id!r} 不存在")
            if job_id is not None:
                job = connection.execute(
                    "SELECT generation_id FROM jobs WHERE id = ?",
                    (job_id,),
                ).fetchone()
                if job is None:
                    raise RecordNotFound(f"job {job_id!r} 不存在")
                if job["generation_id"] != generation_id:
                    raise ControlPlaneError("event 的 job 与 generation 不属于同一发布")
            identifier = self._insert_event(
                connection,
                generation_id=generation_id,
                job_id=job_id,
                event_type=event_type,
                message=message,
                payload_json=payload_json,
                progress_current=progress_current,
                progress_total=progress_total,
                created_at=_now(),
            )
            row = connection.execute(
                "SELECT * FROM control_events WHERE id = ?",
                (identifier,),
            ).fetchone()
            assert row is not None
            return self._event(row)

    def list_events(
        self,
        *,
        generation_id: str | None = None,
        job_id: str | None = None,
        after_id: int | None = None,
        limit: int = 200,
    ) -> list[ControlEvent]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if generation_id is not None:
            clauses.append("generation_id = ?")
            parameters.append(generation_id)
        if job_id is not None:
            clauses.append("job_id = ?")
            parameters.append(job_id)
        if after_id is not None:
            clauses.append("id > ?")
            parameters.append(int(after_id))
        sql = "SELECT * FROM control_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id ASC LIMIT ?"
        parameters.append(max(1, min(int(limit), 2000)))
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._event(row) for row in rows]

    def replace_release_manifest(
        self,
        generation_id: str,
        *,
        documents: Sequence[ManifestDocument],
        chunks: Sequence[ManifestChunk],
    ) -> ManifestSummary:
        document_rows = list(documents)
        chunk_rows = list(chunks)
        document_ids: set[str] = set()
        chunk_ids: set[str] = set()
        counts: dict[str, int] = {}
        for document in document_rows:
            document_id = _required(document.document_id, "document_id")
            _required(document.content_hash, "document content_hash")
            if document_id in document_ids:
                raise ManifestValidationError(f"重复 document_id：{document_id}")
            document_ids.add(document_id)
            counts[document_id] = 0
        for chunk in chunk_rows:
            chunk_id = _required(chunk.chunk_id, "chunk_id")
            document_id = _required(chunk.document_id, "chunk document_id")
            _required(chunk.content_hash, "chunk content_hash")
            if chunk_id in chunk_ids:
                raise ManifestValidationError(f"重复 chunk_id：{chunk_id}")
            if document_id not in document_ids:
                raise ManifestValidationError(
                    f"chunk {chunk_id!r} 引用了清单外文档 {document_id!r}"
                )
            chunk_ids.add(chunk_id)
            counts[document_id] += 1

        with self._transaction(immediate=True) as connection:
            generation = connection.execute(
                "SELECT state FROM generations WHERE id = ?",
                (generation_id,),
            ).fetchone()
            if generation is None:
                raise RecordNotFound(f"generation {generation_id!r} 不存在")
            if generation["state"] != "building":
                raise InvalidStateTransition("只有 building generation 可以修改发布清单")
            connection.execute(
                "DELETE FROM release_chunks WHERE generation_id = ?",
                (generation_id,),
            )
            connection.execute(
                "DELETE FROM release_documents WHERE generation_id = ?",
                (generation_id,),
            )
            connection.executemany(
                """
                INSERT INTO release_documents (
                    generation_id, document_id, content_hash, chunk_count
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        generation_id,
                        document.document_id.strip(),
                        document.content_hash.strip(),
                        counts[document.document_id.strip()],
                    )
                    for document in document_rows
                ],
            )
            connection.executemany(
                """
                INSERT INTO release_chunks (
                    generation_id, chunk_id, document_id, content_hash
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        generation_id,
                        chunk.chunk_id.strip(),
                        chunk.document_id.strip(),
                        chunk.content_hash.strip(),
                    )
                    for chunk in chunk_rows
                ],
            )
            self._insert_event(
                connection,
                generation_id=generation_id,
                job_id=None,
                event_type="manifest.replaced",
                message=(
                    f"发布清单包含 {len(document_rows)} 份文档和 "
                    f"{len(chunk_rows)} 个切片"
                ),
                payload_json=_canonical_json(
                    {
                        "document_count": len(document_rows),
                        "chunk_count": len(chunk_rows),
                    }
                ),
                progress_current=None,
                progress_total=None,
                created_at=_now(),
            )
        return ManifestSummary(generation_id, len(document_rows), len(chunk_rows))

    def get_release_document(
        self,
        generation_id: str,
        document_id: str,
    ) -> ReleaseDocument | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM release_documents
                WHERE generation_id = ? AND document_id = ?
                """,
                (generation_id, document_id),
            ).fetchone()
        if row is None:
            return None
        return ReleaseDocument(
            generation_id=str(row["generation_id"]),
            document_id=str(row["document_id"]),
            content_hash=str(row["content_hash"]),
            chunk_count=int(row["chunk_count"]),
        )

    def list_release_documents(self, generation_id: str) -> list[ReleaseDocument]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM release_documents
                WHERE generation_id = ?
                ORDER BY document_id
                """,
                (generation_id,),
            ).fetchall()
        return [
            ReleaseDocument(
                generation_id=str(row["generation_id"]),
                document_id=str(row["document_id"]),
                content_hash=str(row["content_hash"]),
                chunk_count=int(row["chunk_count"]),
            )
            for row in rows
        ]

    def get_release_chunk(
        self,
        generation_id: str,
        chunk_id: str,
    ) -> ReleaseChunk | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM release_chunks
                WHERE generation_id = ? AND chunk_id = ?
                """,
                (generation_id, chunk_id),
            ).fetchone()
        if row is None:
            return None
        return ReleaseChunk(
            generation_id=str(row["generation_id"]),
            chunk_id=str(row["chunk_id"]),
            document_id=str(row["document_id"]),
            content_hash=str(row["content_hash"]),
        )

    def list_release_chunks(
        self,
        generation_id: str,
        *,
        document_id: str | None = None,
    ) -> list[ReleaseChunk]:
        sql = "SELECT * FROM release_chunks WHERE generation_id = ?"
        parameters: list[Any] = [generation_id]
        if document_id is not None:
            sql += " AND document_id = ?"
            parameters.append(document_id)
        sql += " ORDER BY document_id, chunk_id"
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [
            ReleaseChunk(
                generation_id=str(row["generation_id"]),
                chunk_id=str(row["chunk_id"]),
                document_id=str(row["document_id"]),
                content_hash=str(row["content_hash"]),
            )
            for row in rows
        ]

    def manifest_summary(self, generation_id: str) -> ManifestSummary:
        if self.get_generation(generation_id) is None:
            raise RecordNotFound(f"generation {generation_id!r} 不存在")
        with self._connect() as connection:
            documents = connection.execute(
                "SELECT COUNT(*) AS count FROM release_documents WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
            chunks = connection.execute(
                "SELECT COUNT(*) AS count FROM release_chunks WHERE generation_id = ?",
                (generation_id,),
            ).fetchone()
        assert documents is not None and chunks is not None
        return ManifestSummary(
            generation_id=generation_id,
            document_count=int(documents["count"]),
            chunk_count=int(chunks["count"]),
        )

    def set_active_generation(
        self,
        generation_id: str,
        *,
        alias_name: str,
        observed_collection: str | None = None,
    ) -> ActiveGenerationMirror:
        alias_name = _required(alias_name, "alias_name")
        with self._transaction(immediate=True) as connection:
            generation = connection.execute(
                "SELECT * FROM generations WHERE id = ?",
                (generation_id,),
            ).fetchone()
            if generation is None:
                raise RecordNotFound(f"generation {generation_id!r} 不存在")
            if generation["state"] not in {"passed", "archived", "active"}:
                raise InvalidStateTransition(
                    "只有 passed、archived 或 active generation 可以成为 Active"
                )
            physical_collection = str(generation["physical_collection"])
            observed = (observed_collection or physical_collection).strip()
            if observed != physical_collection:
                raise ControlPlaneError(
                    "Milvus Alias 指向与 generation physical_collection 不一致"
                )
            timestamp = _now()
            previous = connection.execute(
                "SELECT generation_id FROM active_generation_mirror WHERE singleton = 1"
            ).fetchone()
            actual_active = connection.execute(
                "SELECT id FROM generations WHERE state = 'active'"
            ).fetchone()
            previous_id = (
                str(previous["generation_id"])
                if previous
                else str(actual_active["id"])
                if actual_active
                else None
            )
            connection.execute(
                """
                UPDATE generations
                SET state = 'archived', updated_at = ?
                WHERE state = 'active' AND id != ?
                """,
                (timestamp, generation_id),
            )
            connection.execute(
                """
                UPDATE generations
                SET state = 'active', error_message = NULL, updated_at = ?
                WHERE id = ?
                """,
                (timestamp, generation_id),
            )
            connection.execute(
                """
                INSERT INTO active_generation_mirror (
                    singleton, generation_id, alias_name,
                    physical_collection, observed_at
                ) VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    generation_id = excluded.generation_id,
                    alias_name = excluded.alias_name,
                    physical_collection = excluded.physical_collection,
                    observed_at = excluded.observed_at
                """,
                (generation_id, alias_name, physical_collection, timestamp),
            )
            self._insert_event(
                connection,
                generation_id=generation_id,
                job_id=None,
                event_type="release.activated",
                message=f"Alias {alias_name} 已指向 {physical_collection}",
                payload_json=_canonical_json(
                    {
                        "alias_name": alias_name,
                        "physical_collection": physical_collection,
                        "previous_generation_id": previous_id,
                    }
                ),
                progress_current=None,
                progress_total=None,
                created_at=timestamp,
            )
            return ActiveGenerationMirror(
                generation_id=generation_id,
                alias_name=alias_name,
                physical_collection=physical_collection,
                observed_at=timestamp,
            )

    def get_active_generation(self) -> ActiveGenerationMirror | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM active_generation_mirror WHERE singleton = 1"
            ).fetchone()
        if row is None:
            return None
        return ActiveGenerationMirror(
            generation_id=str(row["generation_id"]),
            alias_name=str(row["alias_name"]),
            physical_collection=str(row["physical_collection"]),
            observed_at=str(row["observed_at"]),
        )

    def clear_active_generation(self, *, alias_name: str) -> None:
        alias_name = _required(alias_name, "alias_name")
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM active_generation_mirror WHERE singleton = 1"
            ).fetchone()
            if row is None:
                return
            if row["alias_name"] != alias_name:
                raise ControlPlaneError("alias_name 与当前控制面镜像不一致")
            timestamp = _now()
            generation_id = str(row["generation_id"])
            connection.execute(
                """
                UPDATE generations
                SET state = 'archived', updated_at = ?
                WHERE id = ? AND state = 'active'
                """,
                (timestamp, generation_id),
            )
            connection.execute(
                "DELETE FROM active_generation_mirror WHERE singleton = 1"
            )
            self._insert_event(
                connection,
                generation_id=generation_id,
                job_id=None,
                event_type="release.cleared",
                message=f"Alias {alias_name} 的 Active 镜像已清除",
                payload_json=_canonical_json({"alias_name": alias_name}),
                progress_current=None,
                progress_total=None,
                created_at=timestamp,
            )
