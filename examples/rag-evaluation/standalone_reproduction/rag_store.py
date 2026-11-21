from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import Settings

try:
    from pymilvus import (
        AnnSearchRequest,
        DataType,
        Function,
        FunctionType,
        MilvusClient,
        RRFRanker,
    )
except Exception:  # pragma: no cover - dependency errors are reported at runtime.
    AnnSearchRequest = None  # type: ignore[assignment]
    DataType = None  # type: ignore[assignment]
    Function = None  # type: ignore[assignment]
    FunctionType = None  # type: ignore[assignment]
    MilvusClient = None  # type: ignore[assignment]
    RRFRanker = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)
MAX_MILVUS_CONTENT_BYTES = 65_535
MAX_MILVUS_LEXICAL_BYTES = 65_535
MAX_MILVUS_METADATA_BYTES = 65_536
BM25_ANALYZER_PARAMS = {
    "tokenizer": "jieba",
    "filter": ["lowercase", "cnalphanumonly"],
}
DOCUMENT_COLUMNS = """
    id, name, stored_path, content_type, checksum, file_size,
    character_count, chunk_count, status, error_message,
    embedding_provider, embedding_model, embedding_dimensions,
    created_at, updated_at
"""


class VectorStoreError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _escape_filter_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _clip_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


@dataclass(frozen=True)
class DocumentRecord:
    id: str
    name: str
    stored_path: str
    content_type: str
    checksum: str
    file_size: int
    character_count: int
    chunk_count: int
    status: str
    error_message: str | None
    embedding_provider: str | None
    embedding_model: str | None
    embedding_dimensions: int | None
    created_at: str
    updated_at: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "file_size": self.file_size,
            "character_count": self.character_count,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class ChunkPayload:
    id: str
    position: int
    content: str
    lexical_text: str
    vector: list[float]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SearchResult:
    id: str
    document_id: str
    title: str
    filename: str
    chunk_index: int
    content: str
    score: float
    metadata: dict[str, Any]
    context_content: str | None = None
    trace: dict[str, Any] = field(default_factory=dict)

    def public_dict(self, citation: int) -> dict[str, Any]:
        return {
            "citation": citation,
            "id": self.id,
            "document_id": self.document_id,
            "title": self.title,
            "filename": self.filename,
            "chunk_index": self.chunk_index,
            "snippet": self.context_content or self.content,
            "score": round(self.score, 4),
            "metadata": self.metadata,
            "retrieval": self.trace,
        }


class SQLiteDocumentCatalog:
    def __init__(self, catalog_path: Path) -> None:
        self.catalog_path = catalog_path
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.catalog_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    content_type TEXT NOT NULL DEFAULT '',
                    checksum TEXT NOT NULL UNIQUE,
                    file_size INTEGER NOT NULL,
                    character_count INTEGER NOT NULL DEFAULT 0,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    embedding_provider TEXT,
                    embedding_model TEXT,
                    embedding_dimensions INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _document(row: sqlite3.Row) -> DocumentRecord:
        return DocumentRecord(**dict(row))

    def get_document(self, document_id: str) -> DocumentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {DOCUMENT_COLUMNS} FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
        return self._document(row) if row else None

    def get_by_checksum(self, checksum: str) -> DocumentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {DOCUMENT_COLUMNS} FROM documents WHERE checksum = ?",
                (checksum,),
            ).fetchone()
        return self._document(row) if row else None

    def get_by_name(self, name: str) -> DocumentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {DOCUMENT_COLUMNS} FROM documents WHERE name = ? ORDER BY updated_at DESC LIMIT 1",
                (name,),
            ).fetchone()
        return self._document(row) if row else None

    def stage_document(
        self,
        *,
        name: str,
        stored_path: str,
        content_type: str,
        checksum: str,
        file_size: int,
        character_count: int,
    ) -> tuple[DocumentRecord, bool]:
        """Update desired source state without mutating the active Milvus snapshot."""
        existing = self.get_by_name(name)
        if (
            existing
            and existing.checksum == checksum
            and existing.status not in {"deleted", "removed"}
        ):
            return existing, False
        duplicate = self.get_by_checksum(checksum)
        if duplicate and (existing is None or duplicate.id != existing.id):
            return duplicate, False
        timestamp = _now()
        if existing:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE documents
                    SET name = ?, stored_path = ?, content_type = ?, checksum = ?, file_size = ?,
                        character_count = ?, chunk_count = 0, status = 'staged',
                        error_message = NULL, embedding_provider = NULL,
                        embedding_model = NULL, embedding_dimensions = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        stored_path,
                        content_type,
                        checksum,
                        file_size,
                        character_count,
                        timestamp,
                        existing.id,
                    ),
                )
            staged = self.get_document(existing.id)
            assert staged is not None
            return staged, True
        document_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO documents (
                    id, name, stored_path, content_type, checksum, file_size,
                    character_count, chunk_count, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'staged', ?, ?)
                """,
                (
                    document_id,
                    name,
                    stored_path,
                    content_type,
                    checksum,
                    file_size,
                    character_count,
                    timestamp,
                    timestamp,
                ),
            )
        staged = self.get_document(document_id)
        assert staged is not None
        return staged, True

    def stage_delete(self, document_id: str) -> DocumentRecord | None:
        document = self.get_document(document_id)
        if not document:
            return None
        if document.status == "removed":
            return document
        with self._connect() as connection:
            connection.execute(
                "UPDATE documents SET status = 'deleted', updated_at = ? WHERE id = ?",
                (_now(), document_id),
            )
        return document

    def list_desired_documents(self) -> list[DocumentRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {DOCUMENT_COLUMNS} FROM documents "
                "WHERE status NOT IN ('deleted', 'removed') ORDER BY name"
            ).fetchall()
        return [self._document(row) for row in rows]

    def mark_released(
        self,
        *,
        document_id: str,
        chunk_count: int,
        embedding_provider: str,
        embedding_model: str,
        embedding_dimensions: int,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE documents
                SET chunk_count = ?, status = 'ready', error_message = NULL,
                    embedding_provider = ?, embedding_model = ?,
                    embedding_dimensions = ?, updated_at = ?
                WHERE id = ? AND status NOT IN ('deleted', 'removed')
                """,
                (
                    chunk_count,
                    embedding_provider,
                    embedding_model,
                    embedding_dimensions,
                    _now(),
                    document_id,
                ),
            )

    def mark_unreleased_pending(self, released_document_ids: set[str]) -> int:
        """Keep desired documents outside the active snapshot visibly pending."""
        timestamp = _now()
        with self._connect() as connection:
            if released_document_ids:
                placeholders = ", ".join("?" for _ in released_document_ids)
                cursor = connection.execute(
                    f"""
                    UPDATE documents
                    SET chunk_count = 0, status = 'staged', error_message = NULL,
                        embedding_provider = NULL, embedding_model = NULL,
                        embedding_dimensions = NULL, updated_at = ?
                    WHERE status = 'ready' AND id NOT IN ({placeholders})
                    """,
                    (timestamp, *sorted(released_document_ids)),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE documents
                    SET chunk_count = 0, status = 'staged', error_message = NULL,
                        embedding_provider = NULL, embedding_model = NULL,
                        embedding_dimensions = NULL, updated_at = ?
                    WHERE status = 'ready'
                    """,
                    (timestamp,),
                )
        return max(0, int(cursor.rowcount))

    def finalize_released_deletions(
        self,
        released_document_ids: set[str],
    ) -> int:
        """Mark tombstones absent from the published snapshot as fully removed."""
        timestamp = _now()
        with self._connect() as connection:
            if released_document_ids:
                placeholders = ", ".join("?" for _ in released_document_ids)
                cursor = connection.execute(
                    f"""
                    UPDATE documents
                    SET chunk_count = 0, status = 'removed', error_message = NULL,
                        embedding_provider = NULL, embedding_model = NULL,
                        embedding_dimensions = NULL, updated_at = ?
                    WHERE status = 'deleted' AND id NOT IN ({placeholders})
                    """,
                    (timestamp, *sorted(released_document_ids)),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE documents
                    SET chunk_count = 0, status = 'removed', error_message = NULL,
                        embedding_provider = NULL, embedding_model = NULL,
                        embedding_dimensions = NULL, updated_at = ?
                    WHERE status = 'deleted'
                    """,
                    (timestamp,),
                )
        return max(0, int(cursor.rowcount))

    def create_or_reset_document(
        self,
        *,
        name: str,
        stored_path: str,
        content_type: str,
        checksum: str,
        file_size: int,
    ) -> tuple[DocumentRecord, bool]:
        existing = self.get_by_checksum(checksum)
        if existing and existing.status == "ready":
            return existing, False

        timestamp = _now()
        if existing:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE documents
                    SET name = ?, stored_path = ?, content_type = ?, file_size = ?,
                        character_count = 0, chunk_count = 0, status = 'processing',
                        error_message = NULL, embedding_provider = NULL,
                        embedding_model = NULL, embedding_dimensions = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        stored_path,
                        content_type,
                        file_size,
                        timestamp,
                        existing.id,
                    ),
                )
            refreshed = self.get_document(existing.id)
            assert refreshed is not None
            return refreshed, True

        document_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO documents (
                    id, name, stored_path, content_type, checksum, file_size,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'processing', ?, ?)
                """,
                (
                    document_id,
                    name,
                    stored_path,
                    content_type,
                    checksum,
                    file_size,
                    timestamp,
                    timestamp,
                ),
            )
        created = self.get_document(document_id)
        assert created is not None
        return created, True

    def complete_index(
        self,
        *,
        document_id: str,
        character_count: int,
        chunk_count: int,
        embedding_provider: str,
        embedding_model: str,
        embedding_dimensions: int,
    ) -> DocumentRecord:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE documents
                SET character_count = ?, chunk_count = ?, status = 'ready',
                    error_message = NULL, embedding_provider = ?,
                    embedding_model = ?, embedding_dimensions = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    character_count,
                    chunk_count,
                    embedding_provider,
                    embedding_model,
                    embedding_dimensions,
                    _now(),
                    document_id,
                ),
            )
        document = self.get_document(document_id)
        assert document is not None
        return document

    def mark_failed(self, document_id: str, message: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE documents
                SET status = 'failed', chunk_count = 0, error_message = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (message[:1000], _now(), document_id),
            )

    def mark_all_processing(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE documents
                SET status = 'processing', chunk_count = 0, error_message = NULL,
                    embedding_provider = NULL, embedding_model = NULL,
                    embedding_dimensions = NULL, updated_at = ?
                """,
                (_now(),),
            )

    def list_documents(self) -> list[DocumentRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT {DOCUMENT_COLUMNS} FROM documents "
                "WHERE status != 'removed' ORDER BY created_at DESC"
            ).fetchall()
        return [self._document(row) for row in rows]

    def delete_document(self, document_id: str) -> DocumentRecord | None:
        document = self.get_document(document_id)
        if not document:
            return None
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM documents WHERE id = ?",
                (document_id,),
            )
        return document

    def stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS document_count,
                    COALESCE(SUM(chunk_count), 0) AS chunk_count
                FROM documents
                WHERE status = 'ready'
                """
            ).fetchone()
            embedding = connection.execute(
                """
                SELECT embedding_provider, embedding_model,
                       embedding_dimensions
                FROM documents
                WHERE status = 'ready'
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        return {
            "document_count": int(row["document_count"]),
            "chunk_count": int(row["chunk_count"]),
            "embedding_provider": embedding["embedding_provider"] if embedding else None,
            "embedding_model": embedding["embedding_model"] if embedding else None,
            "embedding_dimensions": (
                embedding["embedding_dimensions"] if embedding else None
            ),
        }

    def embedding_fingerprints(self) -> set[tuple[str, str, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT embedding_provider, embedding_model,
                                embedding_dimensions
                FROM documents
                WHERE status = 'ready'
                """
            ).fetchall()
        return {
            (
                str(row["embedding_provider"]),
                str(row["embedding_model"]),
                int(row["embedding_dimensions"]),
            )
            for row in rows
            if row["embedding_provider"]
            and row["embedding_model"]
            and row["embedding_dimensions"] is not None
        }

    def ready_document_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM documents
                WHERE status = 'ready'
                """
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def uses_versioned_snapshot(self) -> bool:
        """Return whether a promoted immutable generation owns the serving corpus."""
        with self._connect() as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'generations'"
            ).fetchone()
            if not table:
                return False
            row = connection.execute(
                "SELECT 1 FROM generations WHERE state = 'active' LIMIT 1"
            ).fetchone()
        return row is not None

    def serving_document_ids(self) -> list[str] | None:
        # An engineering-stage physical collection is a complete snapshot. Applying the mutable
        # desired catalog as a query filter would make rollback return mixed data.
        return None if self.uses_versioned_snapshot() else self.ready_document_ids()

class MilvusVectorStore:
    def __init__(
        self,
        settings: Settings,
        repo_id: str,
        *,
        client: Any | None = None,
    ) -> None:
        self.uri = settings.milvus_uri
        self.token = settings.milvus_token
        self.database = settings.milvus_database
        self.collection_name = settings.milvus_collection
        self.repo_id = repo_id
        self.timeout = settings.milvus_timeout_seconds
        self.write_batch_size = settings.milvus_write_batch_size
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        if MilvusClient is None:
            raise VectorStoreError(
                "缺少 pymilvus，请先运行 `python -m pip install -r requirements.txt`"
            )
        kwargs: dict[str, Any] = {
            "uri": self.uri,
            "db_name": self.database,
        }
        if self.token:
            kwargs["token"] = self.token
        try:
            self._client = MilvusClient(**kwargs)
        except Exception as exc:
            raise VectorStoreError(f"无法连接 Milvus：{exc}") from exc
        return self._client

    def _has_collection(self) -> bool:
        try:
            if bool(
                self.client.has_collection(
                    collection_name=self.collection_name,
                    timeout=self.timeout,
                )
            ):
                return True
            return self.alias_target(self.collection_name) is not None
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(f"无法检查 Milvus collection：{exc}") from exc

    @staticmethod
    def _listed_alias_names(value: Any) -> set[str]:
        raw_aliases = value.get("aliases", []) if isinstance(value, dict) else value
        if not isinstance(raw_aliases, (list, tuple, set)):
            return set()
        names: set[str] = set()
        for item in raw_aliases:
            if isinstance(item, str):
                names.add(item)
            elif isinstance(item, dict):
                name = item.get("alias") or item.get("name")
                if name:
                    names.add(str(name))
        return names

    def alias_target(self, alias: str | None = None) -> str | None:
        alias_name = alias or self.collection_name
        try:
            list_aliases = getattr(self.client, "list_aliases", None)
            alias_was_listed = False
            if callable(list_aliases):
                listed = list_aliases(timeout=self.timeout)
                if alias_name not in self._listed_alias_names(listed):
                    return None
                alias_was_listed = True
            describe_alias = getattr(self.client, "describe_alias", None)
            if not callable(describe_alias):
                if alias_was_listed:
                    raise RuntimeError("Milvus client cannot describe a listed alias")
                return None
            described = describe_alias(
                alias=alias_name,
                timeout=self.timeout,
            )
        except Exception as exc:
            raise VectorStoreError(f"Unable to read Milvus alias: {exc}") from exc
        target = described.get("collection") or described.get("collection_name")
        return str(target) if target else None

    def point_alias(self, alias: str, collection_name: str) -> str:
        """Create or atomically move an alias, then verify its target."""
        current = self.alias_target(alias)
        try:
            if current is None:
                self.client.create_alias(
                    collection_name=collection_name,
                    alias=alias,
                    timeout=self.timeout,
                )
            elif current != collection_name:
                self.client.alter_alias(
                    collection_name=collection_name,
                    alias=alias,
                    timeout=self.timeout,
                )
        except Exception as exc:
            raise VectorStoreError(f"切换 Milvus alias 失败：{exc}") from exc
        observed = self.alias_target(alias)
        if observed != collection_name:
            raise VectorStoreError(
                f"Milvus alias 校验失败：期望 {collection_name}，实际 {observed or '不存在'}"
            )
        return observed

    def _collection_description(self) -> dict[str, Any]:
        try:
            return self.client.describe_collection(
                collection_name=self.collection_name,
                timeout=self.timeout,
            )
        except Exception as exc:
            raise VectorStoreError(f"无法读取 Milvus collection schema：{exc}") from exc

    def _collection_dimensions(self, description: dict[str, Any] | None = None) -> int:
        description = description or self._collection_description()
        for field in description.get("fields") or []:
            if field.get("name") != "embedding":
                continue
            params = field.get("params") or {}
            value = params.get("dim", field.get("dim"))
            if value is not None:
                return int(value)
        raise VectorStoreError("Milvus collection 缺少 embedding 向量字段")

    @staticmethod
    def _enum_matches(value: Any, expected: Any) -> bool:
        if value == expected:
            return True
        actual_name = str(getattr(value, "name", value)).upper()
        expected_name = str(getattr(expected, "name", expected)).upper()
        return actual_name == expected_name or expected_name in actual_name

    @staticmethod
    def _validate_v2_schema(description: dict[str, Any]) -> None:
        fields = {
            str(field.get("name") or field.get("field_name") or ""): field
            for field in description.get("fields") or []
            if isinstance(field, dict)
        }
        problems: list[str] = []
        expected_types = {
            "id": getattr(DataType, "VARCHAR", "VARCHAR"),
            "repo_id": getattr(DataType, "VARCHAR", "VARCHAR"),
            "document_id": getattr(DataType, "VARCHAR", "VARCHAR"),
            "position": getattr(DataType, "INT64", "INT64"),
            "title": getattr(DataType, "VARCHAR", "VARCHAR"),
            "filename": getattr(DataType, "VARCHAR", "VARCHAR"),
            "content": getattr(DataType, "VARCHAR", "VARCHAR"),
            "lexical_text": getattr(DataType, "VARCHAR", "VARCHAR"),
            "embedding_provider": getattr(DataType, "VARCHAR", "VARCHAR"),
            "embedding_model": getattr(DataType, "VARCHAR", "VARCHAR"),
            "metadata": getattr(DataType, "JSON", "JSON"),
            "embedding": getattr(DataType, "FLOAT_VECTOR", "FLOAT_VECTOR"),
            "bm25_sparse": getattr(
                DataType,
                "SPARSE_FLOAT_VECTOR",
                "SPARSE_FLOAT_VECTOR",
            ),
        }
        for field_name, expected_type in expected_types.items():
            field = fields.get(field_name)
            if field is None:
                problems.append(f"缺少 {field_name} 字段")
                continue
            actual_type = field.get("type", field.get("datatype"))
            if actual_type is None or not MilvusVectorStore._enum_matches(
                actual_type,
                expected_type,
            ):
                problems.append(f"{field_name} 字段类型不正确")

        id_field = fields.get("id")
        if id_field is not None:
            id_params = id_field.get("params") or {}
            is_primary = id_field.get(
                "is_primary",
                id_params.get("is_primary"),
            )
            if str(is_primary).lower() not in {"true", "1"}:
                problems.append("id 字段不是主键")

        embedding_field = fields.get("embedding")
        if embedding_field is not None:
            embedding_params = embedding_field.get("params") or {}
            raw_dimensions = embedding_params.get(
                "dim",
                embedding_field.get("dim"),
            )
            try:
                dimensions = int(raw_dimensions)
            except (TypeError, ValueError):
                dimensions = 0
            if dimensions <= 0:
                problems.append("embedding 字段缺少有效向量维度")

        lexical_field = fields.get("lexical_text")
        if lexical_field is not None:
            params = lexical_field.get("params") or {}
            enabled = params.get(
                "enable_analyzer",
                lexical_field.get("enable_analyzer"),
            )
            if str(enabled).lower() not in {"true", "1"}:
                problems.append("lexical_text 未启用 analyzer")
            analyzer = params.get(
                "analyzer_params",
                lexical_field.get("analyzer_params"),
            )
            if isinstance(analyzer, str):
                try:
                    analyzer = json.loads(analyzer)
                except json.JSONDecodeError:
                    analyzer = None
            if not isinstance(analyzer, dict):
                problems.append("lexical_text 缺少 analyzer_params")
            else:
                filters = set(analyzer.get("filter") or [])
                if analyzer.get("tokenizer") != "jieba" or not {
                    "lowercase",
                    "cnalphanumonly",
                }.issubset(filters):
                    problems.append("lexical_text analyzer 配置不正确")

        functions = [
            item
            for item in description.get("functions") or []
            if isinstance(item, dict)
        ]
        bm25_function = next(
            (
                item
                for item in functions
                if str(item.get("name") or item.get("function_name") or "")
                == "lexical_bm25"
            ),
            None,
        )
        if bm25_function is None:
            problems.append("缺少 lexical_bm25 Function")
        else:
            function_type = bm25_function.get(
                "type",
                bm25_function.get("function_type"),
            )
            expected_bm25 = getattr(FunctionType, "BM25", "BM25")
            if function_type is None or not MilvusVectorStore._enum_matches(
                function_type,
                expected_bm25,
            ):
                problems.append("lexical_bm25 Function 类型不正确")
            if list(bm25_function.get("input_field_names") or []) != [
                "lexical_text"
            ]:
                problems.append("lexical_bm25 输入字段不正确")
            if list(bm25_function.get("output_field_names") or []) != [
                "bm25_sparse"
            ]:
                problems.append("lexical_bm25 输出字段不正确")

        if problems:
            rendered = "、".join(problems)
            raise ValueError(
                f"Milvus collection 不是第 8 章进阶版 RAG Schema：{rendered}。"
                "请执行“重建索引”"
            )

    def _index_descriptions(self) -> list[dict[str, Any]]:
        try:
            names = self.client.list_indexes(
                collection_name=self.collection_name,
                timeout=self.timeout,
            )
            return [
                self.client.describe_index(
                    collection_name=self.collection_name,
                    index_name=str(name),
                    timeout=self.timeout,
                )
                for name in names
            ]
        except Exception as exc:
            raise VectorStoreError(f"无法读取 Milvus collection 索引：{exc}") from exc

    def _validate_v2_indexes(self) -> None:
        indexes = {
            str(item.get("field_name") or ""): item
            for item in self._index_descriptions()
            if isinstance(item, dict)
        }
        problems: list[str] = []
        expected = {
            "embedding": ("AUTOINDEX", "COSINE"),
            "bm25_sparse": ("SPARSE_INVERTED_INDEX", "BM25"),
            "document_id": ("AUTOINDEX", None),
            "repo_id": ("AUTOINDEX", None),
        }
        for field_name, (index_type, metric_type) in expected.items():
            index = indexes.get(field_name)
            if index is None:
                problems.append(f"缺少 {field_name} 索引")
                continue
            if str(index.get("index_type") or "").upper() != index_type:
                problems.append(f"{field_name} index_type 不正确")
            if metric_type is not None and str(
                index.get("metric_type") or ""
            ).upper() != metric_type:
                problems.append(f"{field_name} metric_type 不正确")
        if problems:
            raise ValueError(
                "Milvus collection 不是第 8 章进阶版 RAG 索引配置："
                f"{'、'.join(problems)}。请执行“重建索引”"
            )

    def _validate_v2_collection(self, description: dict[str, Any]) -> None:
        self._validate_v2_schema(description)
        self._validate_v2_indexes()

    def _ensure_collection(self, dimensions: int) -> None:
        if self._has_collection():
            description = self._collection_description()
            self._validate_v2_collection(description)
            existing_dimensions = self._collection_dimensions(description)
            if existing_dimensions != dimensions:
                raise ValueError(
                    "Milvus collection 的向量维度与当前 Embedding 不一致，"
                    "请执行“重建索引”"
                )
            try:
                self.client.load_collection(
                    collection_name=self.collection_name,
                    timeout=self.timeout,
                )
            except Exception as exc:
                raise VectorStoreError(f"无法加载 Milvus collection：{exc}") from exc
            return

        if any(
            item is None
            for item in (MilvusClient, DataType, Function, FunctionType)
        ):
            raise VectorStoreError("pymilvus 不可用，无法创建 Milvus collection")
        schema = MilvusClient.create_schema(
            auto_id=False,
            enable_dynamic_field=False,
        )
        schema.add_field(
            field_name="id",
            datatype=DataType.VARCHAR,
            is_primary=True,
            max_length=128,
        )
        schema.add_field(
            field_name="repo_id",
            datatype=DataType.VARCHAR,
            max_length=128,
        )
        schema.add_field(
            field_name="document_id",
            datatype=DataType.VARCHAR,
            max_length=64,
        )
        schema.add_field(field_name="position", datatype=DataType.INT64)
        schema.add_field(
            field_name="title",
            datatype=DataType.VARCHAR,
            max_length=1024,
        )
        schema.add_field(
            field_name="filename",
            datatype=DataType.VARCHAR,
            max_length=1024,
        )
        schema.add_field(
            field_name="content",
            datatype=DataType.VARCHAR,
            max_length=MAX_MILVUS_CONTENT_BYTES,
        )
        schema.add_field(
            field_name="lexical_text",
            datatype=DataType.VARCHAR,
            max_length=MAX_MILVUS_LEXICAL_BYTES,
            enable_analyzer=True,
            analyzer_params=BM25_ANALYZER_PARAMS,
        )
        schema.add_field(
            field_name="embedding_provider",
            datatype=DataType.VARCHAR,
            max_length=64,
        )
        schema.add_field(
            field_name="embedding_model",
            datatype=DataType.VARCHAR,
            max_length=256,
        )
        schema.add_field(field_name="metadata", datatype=DataType.JSON)
        schema.add_field(
            field_name="embedding",
            datatype=DataType.FLOAT_VECTOR,
            dim=dimensions,
        )
        schema.add_field(
            field_name="bm25_sparse",
            datatype=DataType.SPARSE_FLOAT_VECTOR,
        )
        schema.add_function(
            Function(
                name="lexical_bm25",
                function_type=FunctionType.BM25,
                input_field_names=["lexical_text"],
                output_field_names=["bm25_sparse"],
            )
        )

        index_params = MilvusClient.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_name="embedding_cosine",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        index_params.add_index(
            field_name="bm25_sparse",
            index_name="lexical_bm25_index",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
            params={
                "inverted_index_algo": "DAAT_MAXSCORE",
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
            },
        )
        index_params.add_index(
            field_name="document_id",
            index_name="document_id_scalar",
            index_type="AUTOINDEX",
        )
        index_params.add_index(
            field_name="repo_id",
            index_name="repo_id_scalar",
            index_type="AUTOINDEX",
        )
        try:
            self.client.create_collection(
                collection_name=self.collection_name,
                schema=schema,
                index_params=index_params,
                consistency_level="Strong",
                timeout=self.timeout,
            )
        except Exception as exc:
            raise VectorStoreError(f"无法创建 Milvus collection：{exc}") from exc

    def _document_filter(self, document_id: str) -> str:
        return (
            f'repo_id == "{_escape_filter_value(self.repo_id)}" '
            f'and document_id == "{_escape_filter_value(document_id)}"'
        )

    def replace_document(
        self,
        *,
        document_id: str,
        filename: str,
        title: str,
        chunks: list[ChunkPayload],
        embedding_provider: str,
        embedding_model: str,
    ) -> int:
        if not chunks:
            raise ValueError("文档切片为空")
        dimensions = len(chunks[0].vector)
        if dimensions <= 0 or any(len(item.vector) != dimensions for item in chunks):
            raise ValueError("Embedding 返回了不一致的向量维度")
        if len({item.id for item in chunks}) != len(chunks):
            raise ValueError("文档切片包含重复 chunk_id")
        for item in chunks:
            if len(item.content.encode("utf-8")) > MAX_MILVUS_CONTENT_BYTES:
                raise ValueError("单个切片超过 Milvus VARCHAR 的 65535 字节限制")
            if len(item.lexical_text.encode("utf-8")) > MAX_MILVUS_LEXICAL_BYTES:
                raise ValueError("单个切片的 lexical_text 超过 Milvus VARCHAR 限制")
            if (
                len(json.dumps(item.metadata, ensure_ascii=False).encode("utf-8"))
                > MAX_MILVUS_METADATA_BYTES
            ):
                raise ValueError("单个切片的 metadata 超过 Milvus JSON 字段限制")

        self._ensure_collection(dimensions)
        self.delete_document(document_id)
        rows = [
            {
                "id": item.id,
                "repo_id": self.repo_id,
                "document_id": document_id,
                "position": item.position,
                "title": _clip_utf8(title, 1024),
                "filename": _clip_utf8(filename, 1024),
                "content": item.content,
                "lexical_text": item.lexical_text,
                "embedding_provider": _clip_utf8(embedding_provider, 64),
                "embedding_model": _clip_utf8(embedding_model, 256),
                "metadata": item.metadata,
                "embedding": [float(value) for value in item.vector],
            }
            for item in chunks
        ]
        try:
            for start in range(0, len(rows), self.write_batch_size):
                batch = rows[start : start + self.write_batch_size]
                result = self.client.insert(
                    collection_name=self.collection_name,
                    data=batch,
                    timeout=self.timeout,
                )
                insert_count = result.get("insert_count")
                if insert_count is not None and int(insert_count) != len(batch):
                    raise VectorStoreError(
                        f"Milvus 只写入了 {insert_count}/{len(batch)} 条切片"
                    )
            self.client.flush(
                collection_name=self.collection_name,
                timeout=self.timeout,
            )
            count_rows = self.client.query(
                collection_name=self.collection_name,
                filter=self._document_filter(document_id),
                output_fields=["count(*)"],
                timeout=self.timeout,
            )
            stored_count = (
                int(count_rows[0].get("count(*)", -1))
                if count_rows
                else -1
            )
            if stored_count != len(rows):
                raise VectorStoreError(
                    f"Milvus 写入校验失败：期望 {len(rows)} 条，实际 {stored_count} 条"
                )
        except Exception as exc:
            try:
                self.delete_document(document_id)
            except Exception:
                logger.exception("清理 Milvus 部分写入失败")
            if isinstance(exc, (ValueError, VectorStoreError)):
                raise
            raise VectorStoreError(f"写入 Milvus 失败：{exc}") from exc
        return dimensions

    def delete_document(self, document_id: str) -> None:
        if not self._has_collection():
            return
        try:
            self.client.delete(
                collection_name=self.collection_name,
                filter=self._document_filter(document_id),
                timeout=self.timeout,
            )
        except Exception as exc:
            raise VectorStoreError(f"删除 Milvus 文档切片失败：{exc}") from exc

    def list_chunks(self, document_id: str) -> list[dict[str, Any]]:
        if not self._has_collection():
            return []
        try:
            if hasattr(self.client, "query_iterator"):
                iterator = self.client.query_iterator(
                    collection_name=self.collection_name,
                    filter=self._document_filter(document_id),
                    output_fields=["id", "position", "content", "metadata"],
                    batch_size=1000,
                    limit=-1,
                    timeout=self.timeout,
                )
                rows = []
                try:
                    while batch := iterator.next():
                        rows.extend(batch)
                finally:
                    iterator.close()
            else:
                rows = self.client.query(
                    collection_name=self.collection_name,
                    filter=self._document_filter(document_id),
                    output_fields=["id", "position", "content", "metadata"],
                    limit=16_384,
                    timeout=self.timeout,
                )
        except Exception as exc:
            raise VectorStoreError(f"读取 Milvus 文档切片失败：{exc}") from exc
        rows.sort(key=lambda row: int(row.get("position") or 0))
        return [
            {
                "id": str(row.get("id") or ""),
                "position": int(row.get("position") or 0),
                "content": str(row.get("content") or ""),
                "character_count": len(str(row.get("content") or "")),
                "metadata": dict(row.get("metadata") or {}),
            }
            for row in rows
        ]

    def list_parent_chunks(
        self,
        document_id: str,
        parent_id: str,
    ) -> list[dict[str, Any]]:
        clean_parent_id = parent_id.strip()
        if not clean_parent_id or not self._has_collection():
            return []
        expression = (
            f"{self._document_filter(document_id)} "
            f'and metadata["parent_id"] == "{_escape_filter_value(clean_parent_id)}"'
        )
        try:
            rows = self.client.query(
                collection_name=self.collection_name,
                filter=expression,
                output_fields=["id", "position", "content", "metadata"],
                limit=16_384,
                timeout=self.timeout,
            )
        except Exception as exc:
            raise VectorStoreError(f"读取 Milvus 父级上下文失败：{exc}") from exc
        rows.sort(
            key=lambda row: (
                int((row.get("metadata") or {}).get("child_index") or 0),
                int(row.get("position") or 0),
            )
        )
        return [
            {
                "id": str(row.get("id") or ""),
                "position": int(row.get("position") or 0),
                "content": str(row.get("content") or ""),
                "metadata": dict(row.get("metadata") or {}),
            }
            for row in rows
        ]

    def scan_rows(
        self,
        *,
        document_id: str | None = None,
        include_vectors: bool = False,
    ) -> list[dict[str, Any]]:
        """Scan an immutable collection for reuse, validation, or reconciliation."""
        if not self._has_collection():
            return []
        expression = (
            f'repo_id == "{_escape_filter_value(self.repo_id)}"'
        )
        if document_id:
            expression += (
                f' and document_id == "{_escape_filter_value(document_id)}"'
            )
        output_fields = [
            "id",
            "repo_id",
            "document_id",
            "position",
            "title",
            "filename",
            "content",
            "lexical_text",
            "embedding_provider",
            "embedding_model",
            "metadata",
        ]
        if include_vectors:
            output_fields.append("embedding")
        try:
            if hasattr(self.client, "query_iterator"):
                iterator = self.client.query_iterator(
                    collection_name=self.collection_name,
                    filter=expression,
                    output_fields=output_fields,
                    batch_size=1000,
                    limit=-1,
                    timeout=self.timeout,
                )
                rows: list[dict[str, Any]] = []
                try:
                    while batch := iterator.next():
                        rows.extend(self._row_dict(row) for row in batch)
                finally:
                    iterator.close()
            else:
                rows = [
                    self._row_dict(row)
                    for row in self.client.query(
                        collection_name=self.collection_name,
                        filter=expression,
                        output_fields=output_fields,
                        limit=16_384,
                        timeout=self.timeout,
                    )
                ]
        except Exception as exc:
            raise VectorStoreError(f"扫描 Milvus collection 失败：{exc}") from exc
        return rows

    @staticmethod
    def _row_dict(row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            return dict(row)
        to_dict = getattr(row, "to_dict", None)
        if callable(to_dict):
            value = to_dict()
            if isinstance(value, dict):
                return dict(value)
        return dict(row)

    def validate_ready(self) -> dict[str, Any]:
        if not self._has_collection():
            raise VectorStoreError("候选 Milvus collection 不存在")
        description = self._collection_description()
        self._validate_v2_collection(description)
        rows = self.client.query(
            collection_name=self.collection_name,
            filter=(
                f'repo_id == "{_escape_filter_value(self.repo_id)}"'
            ),
            output_fields=["count(*)"],
            consistency_level="Strong",
            timeout=self.timeout,
        )
        return {
            "schema_ready": True,
            "dimensions": self._collection_dimensions(description),
            "row_count": int(rows[0].get("count(*)", 0)) if rows else 0,
        }

    @staticmethod
    def _output_fields() -> list[str]:
        return [
            "document_id",
            "position",
            "title",
            "filename",
            "content",
            "metadata",
        ]

    def _search_filter(
        self,
        ready_document_ids: list[str] | None,
        embedding_provider: str,
        embedding_model: str,
    ) -> str:
        expression = (
            f'repo_id == "{_escape_filter_value(self.repo_id)}" '
            f'and embedding_provider == "{_escape_filter_value(embedding_provider)}" '
            f'and embedding_model == "{_escape_filter_value(embedding_model)}"'
        )
        if ready_document_ids is None:
            return expression
        quoted_ids = ", ".join(
            f'"{_escape_filter_value(document_id)}"'
            for document_id in ready_document_ids
        )
        return f"{expression} and document_id in [{quoted_ids}]"

    @staticmethod
    def _route_scores(raw_results: list[list[dict[str, Any]]]) -> dict[str, tuple[int, float]]:
        hits = raw_results[0] if raw_results else []
        return {
            str(hit.get("id") or (hit.get("entity") or {}).get("id") or ""): (
                rank,
                float(hit.get("distance") or 0.0),
            )
            for rank, hit in enumerate(hits, start=1)
        }

    @staticmethod
    def _search_result(
        hit: dict[str, Any],
        *,
        score: float,
        trace: dict[str, Any],
    ) -> SearchResult:
        entity = hit.get("entity") or {}
        return SearchResult(
            id=str(hit.get("id") or entity.get("id") or ""),
            document_id=str(entity.get("document_id") or ""),
            title=str(entity.get("title") or ""),
            filename=str(entity.get("filename") or ""),
            chunk_index=int(entity.get("position") or 0),
            content=str(entity.get("content") or ""),
            score=score,
            metadata=dict(entity.get("metadata") or {}),
            trace=trace,
        )

    def search(
        self,
        query_text: str,
        query_vector: list[float],
        top_k: int,
        *,
        mode: str,
        dense_candidate_k: int,
        keyword_candidate_k: int,
        rrf_k: int,
        ready_document_ids: list[str] | None,
        embedding_provider: str,
        embedding_model: str,
    ) -> list[SearchResult]:
        if not query_text.strip() or not query_vector or ready_document_ids == []:
            return []
        if not self._has_collection():
            raise VectorStoreError(
                "SQLite 目录中存在已入库文档，但 Milvus collection 不存在，"
                "请执行“重建索引”"
            )
        description = self._collection_description()
        self._validate_v2_collection(description)
        dimensions = self._collection_dimensions(description)
        if dimensions != len(query_vector):
            raise ValueError(
                "查询向量维度与 Milvus collection 不一致，请执行“重建索引”"
            )

        expression = self._search_filter(
            ready_document_ids,
            embedding_provider,
            embedding_model,
        )
        dense_limit = max(top_k, dense_candidate_k)
        dense_data = [[float(value) for value in query_vector]]
        try:
            dense_raw = self.client.search(
                collection_name=self.collection_name,
                data=dense_data,
                anns_field="embedding",
                filter=expression,
                limit=dense_limit,
                output_fields=self._output_fields(),
                search_params={"metric_type": "COSINE", "params": {}},
                timeout=self.timeout,
            )
        except Exception as exc:
            raise VectorStoreError(f"Milvus 稠密向量检索失败：{exc}") from exc

        dense_hits = dense_raw[0] if dense_raw else []
        dense_scores = self._route_scores(dense_raw)
        if mode == "dense":
            return [
                self._search_result(
                    hit,
                    score=float(hit.get("distance") or 0.0),
                    trace={
                        "retrieval_mode": "dense",
                        "sources": ["dense"],
                        "dense_rank": rank,
                        "dense_score": round(float(hit.get("distance") or 0.0), 6),
                    },
                )
                for rank, hit in enumerate(dense_hits[:top_k], start=1)
            ]

        if any(item is None for item in (AnnSearchRequest, RRFRanker)):
            raise VectorStoreError("当前 pymilvus 不支持 Milvus 混合检索")
        keyword_limit = max(top_k, keyword_candidate_k)
        try:
            keyword_raw = self.client.search(
                collection_name=self.collection_name,
                data=[query_text],
                anns_field="bm25_sparse",
                filter=expression,
                limit=keyword_limit,
                output_fields=self._output_fields(),
                search_params={"metric_type": "BM25", "params": {}},
                timeout=self.timeout,
            )
            requests = [
                AnnSearchRequest(
                    data=dense_data,
                    anns_field="embedding",
                    param={"metric_type": "COSINE", "params": {}},
                    limit=dense_limit,
                    filter=expression,
                ),
                AnnSearchRequest(
                    data=[query_text],
                    anns_field="bm25_sparse",
                    param={"metric_type": "BM25", "params": {}},
                    limit=keyword_limit,
                    filter=expression,
                ),
            ]
            hybrid_raw = self.client.hybrid_search(
                collection_name=self.collection_name,
                reqs=requests,
                ranker=RRFRanker(k=rrf_k),
                limit=top_k,
                output_fields=self._output_fields(),
                timeout=self.timeout,
            )
        except Exception as exc:
            raise VectorStoreError(f"Milvus Dense + BM25 混合检索失败：{exc}") from exc

        keyword_scores = self._route_scores(keyword_raw)
        hybrid_hits = hybrid_raw[0] if hybrid_raw else []
        results: list[SearchResult] = []
        for hit in hybrid_hits:
            item_id = str(hit.get("id") or (hit.get("entity") or {}).get("id") or "")
            dense = dense_scores.get(item_id)
            keyword = keyword_scores.get(item_id)
            sources = [
                route
                for route, value in (("dense", dense), ("bm25", keyword))
                if value is not None
            ]
            fusion_score = float(hit.get("distance") or 0.0)
            results.append(
                self._search_result(
                    hit,
                    score=fusion_score,
                    trace={
                        "retrieval_mode": "hybrid",
                        "sources": sources,
                        "dense_rank": dense[0] if dense else None,
                        "dense_score": round(dense[1], 6) if dense else None,
                        "bm25_rank": keyword[0] if keyword else None,
                        "bm25_score": round(keyword[1], 6) if keyword else None,
                        "fusion_method": "rrf",
                        "rrf_k": rrf_k,
                        "fusion_score": round(fusion_score, 6),
                    },
                )
            )
        return results

    def reset(self) -> None:
        if not self._has_collection():
            return
        try:
            self.client.drop_collection(
                collection_name=self.collection_name,
                timeout=self.timeout,
            )
        except Exception as exc:
            raise VectorStoreError(f"重置 Milvus collection 失败：{exc}") from exc

    def status(self) -> dict[str, Any]:
        try:
            exists = self._has_collection()
            alias_target = self.alias_target(self.collection_name)
            dimensions = None
            schema_ready = False
            if exists:
                description = self._collection_description()
                dimensions = self._collection_dimensions(description)
                try:
                    self._validate_v2_collection(description)
                    schema_ready = True
                except ValueError:
                    schema_ready = False
            return {
                "available": True,
                "backend": "Milvus",
                "uri": self.uri,
                "database": self.database,
                "collection": self.collection_name,
                "alias_target": alias_target,
                "dimensions": dimensions,
                "schema_version": "production-rag-v3" if schema_ready else None,
                "dense_search": schema_ready,
                "bm25_search": schema_ready,
                "detail": (
                    "Milvus 已连接，Dense 与 BM25 索引已就绪"
                    if exists and schema_ready
                    else "Milvus alias 指向的 collection 需要重建为 RAG 工程化 Schema"
                    if exists
                    else "Milvus 已连接，尚未发布可检索版本"
                ),
            }
        except Exception as exc:
            return {
                "available": False,
                "backend": "Milvus",
                "uri": self.uri,
                "database": self.database,
                "collection": self.collection_name,
                "alias_target": None,
                "dimensions": None,
                "schema_version": None,
                "dense_search": False,
                "bm25_search": False,
                "detail": str(exc),
            }

    def close(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            self._client.close()


class RAGStore:
    def __init__(
        self,
        settings: Settings,
        repo_id: str = "devflow-ai",
        *,
        vector_store: MilvusVectorStore | Any | None = None,
        snapshot_mode: bool = False,
    ) -> None:
        self.repo_id = repo_id
        self.catalog = SQLiteDocumentCatalog(settings.catalog_path)
        self.vectors = vector_store or MilvusVectorStore(settings, repo_id)
        self.snapshot_mode = snapshot_mode

    def get_document(self, document_id: str) -> DocumentRecord | None:
        return self.catalog.get_document(document_id)

    def get_by_checksum(self, checksum: str) -> DocumentRecord | None:
        return self.catalog.get_by_checksum(checksum)

    def create_or_reset_document(
        self,
        *,
        name: str,
        stored_path: str,
        content_type: str,
        checksum: str,
        file_size: int,
    ) -> tuple[DocumentRecord, bool]:
        existing = self.catalog.get_by_checksum(checksum)
        if existing and existing.status == "ready":
            return existing, False
        if existing:
            self.vectors.delete_document(existing.id)
        return self.catalog.create_or_reset_document(
            name=name,
            stored_path=stored_path,
            content_type=content_type,
            checksum=checksum,
            file_size=file_size,
        )

    def replace_chunks(
        self,
        *,
        document_id: str,
        filename: str,
        title: str,
        character_count: int,
        chunks: list[ChunkPayload],
        embedding_provider: str,
        embedding_model: str,
    ) -> DocumentRecord:
        dimensions = self.vectors.replace_document(
            document_id=document_id,
            filename=filename,
            title=title,
            chunks=chunks,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
        )
        try:
            document = self.catalog.complete_index(
                document_id=document_id,
                character_count=character_count,
                chunk_count=len(chunks),
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                embedding_dimensions=dimensions,
            )
        except Exception:
            self.vectors.delete_document(document_id)
            raise
        return document

    def mark_failed(self, document_id: str, message: str) -> None:
        self.catalog.mark_failed(document_id, message)
        try:
            self.vectors.delete_document(document_id)
        except Exception:
            logger.exception("清理失败文档的 Milvus 切片失败")

    def list_documents(self) -> list[DocumentRecord]:
        return self.catalog.list_documents()

    def list_chunks(self, document_id: str) -> list[dict[str, Any]]:
        document = self.catalog.get_document(document_id)
        chunks = self.vectors.list_chunks(document_id)
        if document and document.status == "ready" and not chunks:
            raise VectorStoreError(
                "文档目录显示已入库，但 Milvus 中没有对应切片，请执行“重建索引”"
            )
        return chunks

    def list_parent_chunks(
        self,
        document_id: str,
        parent_id: str,
    ) -> list[dict[str, Any]]:
        return self.vectors.list_parent_chunks(document_id, parent_id)

    def delete_document(self, document_id: str) -> DocumentRecord | None:
        document = self.catalog.get_document(document_id)
        if not document:
            return None
        self.vectors.delete_document(document_id)
        return self.catalog.delete_document(document_id)

    def stats(self) -> dict[str, Any]:
        return self.catalog.stats()

    def embedding_fingerprints(self) -> set[tuple[str, str, int]]:
        if self.snapshot_mode or self.catalog.uses_versioned_snapshot():
            # The immutable generation validates its own model fingerprint during
            # build. Milvus dimension and metadata checks remain authoritative.
            return set()
        return self.catalog.embedding_fingerprints()

    def search(
        self,
        query_text: str,
        query_vector: list[float],
        top_k: int,
        *,
        mode: str,
        dense_candidate_k: int,
        keyword_candidate_k: int,
        rrf_k: int,
        embedding_provider: str,
        embedding_model: str,
    ) -> list[SearchResult]:
        return self.vectors.search(
            query_text,
            query_vector,
            top_k,
            mode=mode,
            dense_candidate_k=dense_candidate_k,
            keyword_candidate_k=keyword_candidate_k,
            rrf_k=rrf_k,
            ready_document_ids=(
                None if self.snapshot_mode else self.catalog.serving_document_ids()
            ),
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
        )

    def reset_vector_index(self) -> None:
        self.vectors.reset()
        self.catalog.mark_all_processing()

    def vector_status(self) -> dict[str, Any]:
        return self.vectors.status()

    def close(self) -> None:
        self.vectors.close()
