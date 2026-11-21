from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

import rag_store as rag_store_module
from config import Settings
from model_client import ModelClient
from rag_pipeline import RAGPipeline
from rag_store import (
    BM25_ANALYZER_PARAMS,
    ChunkPayload,
    DocumentRecord,
    MilvusVectorStore,
    RAGStore,
    SearchResult,
)
from reranker import Reranker


def settings_for(tmp_path: Path, **changes: Any) -> Settings:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        provider="deterministic",
        base_url="http://127.0.0.1:11434",
        api_key="",
        embedding_model="deterministic-hash-v1",
        chat_model="unused",
        knowledge_base_name="advanced-rag-test",
        chunk_size=220,
        chunk_overlap=40,
        top_k=4,
        min_relevance_score=0.35,
        max_upload_bytes=1024 * 1024,
        model_timeout_seconds=5,
        embedding_dimensions=384,
        embedding_batch_size=16,
        catalog_path=tmp_path / "rag.sqlite3",
        upload_dir=upload_dir,
        milvus_uri="http://127.0.0.1:19530",
        milvus_token="",
        milvus_database="default",
        milvus_collection="advanced_test_chunks",
        milvus_timeout_seconds=5,
        milvus_write_batch_size=2,
        retrieval_mode="hybrid_rerank",
        dense_candidate_k=8,
        keyword_candidate_k=9,
        rerank_candidate_k=10,
        rrf_k=60,
        rerank_provider="qwen_api",
        rerank_base_url="https://rerank.test/compatible-api/v1",
        rerank_api_key="",
        rerank_model="qwen3-rerank",
        rerank_instruction="Rank passages for the question.",
        rerank_timeout_seconds=5,
        rerank_min_score=0.15,
        context_chunk_max_chars=900,
    )
    return replace(settings, **changes)


def search_result(
    item_id: str,
    content: str,
    *,
    document_id: str = "doc-1",
    chunk_index: int = 1,
    score: float = 0.03,
    metadata: dict[str, Any] | None = None,
    trace: dict[str, Any] | None = None,
) -> SearchResult:
    return SearchResult(
        id=item_id,
        document_id=document_id,
        title="运行手册",
        filename="runbook.md",
        chunk_index=chunk_index,
        content=content,
        score=score,
        metadata=metadata or {},
        trace=trace
        or {
            "retrieval_mode": "hybrid",
            "sources": ["dense", "bm25"],
            "dense_rank": 1,
            "dense_score": 0.82,
            "bm25_rank": 1,
            "bm25_score": 3.2,
            "fusion_method": "rrf",
            "fusion_score": score,
        },
    )


class FakeDataType:
    VARCHAR = "VARCHAR"
    INT64 = "INT64"
    JSON = "JSON"
    FLOAT_VECTOR = "FLOAT_VECTOR"
    SPARSE_FLOAT_VECTOR = "SPARSE_FLOAT_VECTOR"


class FakeFunctionType:
    BM25 = "BM25"


class FakeFunction:
    def __init__(self, **options: Any) -> None:
        self.options = options


class FakeSchema:
    def __init__(self, **options: Any) -> None:
        self.options = options
        self.fields: list[dict[str, Any]] = []
        self.functions: list[FakeFunction] = []

    def add_field(self, **field: Any) -> None:
        self.fields.append(field)

    def add_function(self, function: FakeFunction) -> None:
        self.functions.append(function)


class FakeIndexParams:
    def __init__(self) -> None:
        self.indexes: list[dict[str, Any]] = []

    def add_index(self, **index: Any) -> None:
        self.indexes.append(index)


class FakeMilvusSDK:
    @staticmethod
    def create_schema(**options: Any) -> FakeSchema:
        return FakeSchema(**options)

    @staticmethod
    def prepare_index_params() -> FakeIndexParams:
        return FakeIndexParams()


class FakeAnnSearchRequest:
    def __init__(self, **options: Any) -> None:
        self.options = options


class FakeRRFRanker:
    def __init__(self, k: int) -> None:
        self.k = k


def v2_description(dimensions: int, *, include_function: bool = True) -> dict[str, Any]:
    description: dict[str, Any] = {
        "fields": [
            {"name": "id", "type": "VARCHAR", "is_primary": True, "params": {}},
            {"name": "repo_id", "type": "VARCHAR", "params": {}},
            {"name": "document_id", "type": "VARCHAR", "params": {}},
            {"name": "position", "type": "INT64", "params": {}},
            {"name": "title", "type": "VARCHAR", "params": {}},
            {"name": "filename", "type": "VARCHAR", "params": {}},
            {"name": "content", "type": "VARCHAR", "params": {}},
            {"name": "lexical_text", "type": "VARCHAR", "params": {
                "enable_analyzer": True,
                "analyzer_params": BM25_ANALYZER_PARAMS,
            }},
            {"name": "embedding_provider", "type": "VARCHAR", "params": {}},
            {"name": "embedding_model", "type": "VARCHAR", "params": {}},
            {"name": "metadata", "type": "JSON", "params": {}},
            {"name": "embedding", "type": "FLOAT_VECTOR", "params": {"dim": dimensions}},
            {"name": "bm25_sparse", "type": "SPARSE_FLOAT_VECTOR", "params": {}},
        ],
        "functions": [],
    }
    if include_function:
        description["functions"] = [
            {
                "name": "lexical_bm25",
                "type": "BM25",
                "input_field_names": ["lexical_text"],
                "output_field_names": ["bm25_sparse"],
            }
        ]
    return description


def hit(item_id: str, distance: float, content: str, position: int) -> dict[str, Any]:
    return {
        "id": item_id,
        "distance": distance,
        "entity": {
            "document_id": "doc-1",
            "position": position,
            "title": "运行手册",
            "filename": "runbook.md",
            "content": content,
            "metadata": {"parent_id": "parent-a", "child_index": position - 1},
        },
    }


class RecordingMilvusClient:
    def __init__(
        self,
        dimensions: int | None = None,
        *,
        include_function: bool = True,
    ) -> None:
        self.dimensions = dimensions
        self.include_function = include_function
        self.inserted: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []
        self.hybrid_calls: list[dict[str, Any]] = []
        self.query_calls: list[dict[str, Any]] = []
        self.parent_rows: list[dict[str, Any]] = []

    def has_collection(self, **_: Any) -> bool:
        return self.dimensions is not None

    def describe_collection(self, **_: Any) -> dict[str, Any]:
        if self.dimensions is None:
            raise RuntimeError("collection missing")
        return v2_description(self.dimensions, include_function=self.include_function)

    def list_indexes(self, **_: Any) -> list[str]:
        return [
            "embedding_cosine",
            "lexical_bm25_index",
            "document_id_scalar",
            "repo_id_scalar",
        ]

    def describe_index(self, *, index_name: str, **_: Any) -> dict[str, Any]:
        if index_name == "embedding_cosine":
            return {
                "field_name": "embedding",
                "index_type": "AUTOINDEX",
                "metric_type": "COSINE",
            }
        if index_name == "lexical_bm25_index":
            return {
                "field_name": "bm25_sparse",
                "index_type": "SPARSE_INVERTED_INDEX",
                "metric_type": "BM25",
            }
        return {
            "field_name": (
                "document_id"
                if index_name == "document_id_scalar"
                else "repo_id"
            ),
            "index_type": "AUTOINDEX",
        }

    def create_collection(self, *, schema: FakeSchema, **options: Any) -> None:
        self.create_calls.append({"schema": schema, **options})
        vector_field = next(
            field for field in schema.fields if field["field_name"] == "embedding"
        )
        self.dimensions = int(vector_field["dim"])

    def load_collection(self, **_: Any) -> None:
        pass

    def insert(self, *, data: list[dict[str, Any]], **_: Any) -> dict[str, int]:
        self.inserted.extend(data)
        return {"insert_count": len(data)}

    def flush(self, **_: Any) -> None:
        pass

    def delete(self, **_: Any) -> None:
        self.inserted.clear()

    def query(self, **options: Any) -> list[dict[str, Any]]:
        self.query_calls.append(options)
        if options.get("output_fields") == ["count(*)"]:
            return [{"count(*)": len(self.inserted)}]
        return [dict(row) for row in self.parent_rows]

    def search(self, **options: Any) -> list[list[dict[str, Any]]]:
        self.search_calls.append(options)
        if options["anns_field"] == "embedding":
            return [[
                hit("dense-only", 0.91, "语义相近的说明", 1),
                hit("both", 0.83, "错误率阈值说明", 2),
            ]]
        return [[
            hit("keyword-only", 5.2, "ERR_AUTH_401 处理步骤", 3),
            hit("both", 2.7, "错误率阈值说明", 2),
        ]]

    def hybrid_search(self, **options: Any) -> list[list[dict[str, Any]]]:
        self.hybrid_calls.append(options)
        return [[
            hit("keyword-only", 0.032, "ERR_AUTH_401 处理步骤", 3),
            hit("both", 0.031, "错误率阈值说明", 2),
        ]]

    def list_collections(self, **_: Any) -> list[str]:
        return ["advanced_test_chunks"] if self.dimensions is not None else []


def install_fake_milvus_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rag_store_module, "MilvusClient", FakeMilvusSDK)
    monkeypatch.setattr(rag_store_module, "DataType", FakeDataType)
    monkeypatch.setattr(rag_store_module, "FunctionType", FakeFunctionType)
    monkeypatch.setattr(rag_store_module, "Function", FakeFunction)
    monkeypatch.setattr(rag_store_module, "AnnSearchRequest", FakeAnnSearchRequest)
    monkeypatch.setattr(rag_store_module, "RRFRanker", FakeRRFRanker)


def test_milvus_v2_schema_builds_bm25_function_and_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_milvus_sdk(monkeypatch)
    client = RecordingMilvusClient()
    vectors = MilvusVectorStore(settings_for(tmp_path), "test-repo", client=client)
    chunks = [
        ChunkPayload(
            id="chunk-1",
            position=1,
            content="发生 ERR_AUTH_401 时刷新令牌一次。",
            lexical_text="认证手册\nERR_AUTH_401\n刷新令牌",
            vector=[1.0, 0.0, 0.0],
            metadata={"parent_id": "parent-a", "child_index": 0},
        )
    ]

    dimensions = vectors.replace_document(
        document_id="doc-1",
        filename="runbook.md",
        title="运行手册",
        chunks=chunks,
        embedding_provider="test",
        embedding_model="test-model",
    )

    assert dimensions == 3
    create = client.create_calls[0]
    schema = create["schema"]
    lexical = next(field for field in schema.fields if field["field_name"] == "lexical_text")
    assert lexical["enable_analyzer"] is True
    assert lexical["analyzer_params"] == BM25_ANALYZER_PARAMS
    assert any(field["field_name"] == "bm25_sparse" for field in schema.fields)
    assert schema.functions[0].options == {
        "name": "lexical_bm25",
        "function_type": "BM25",
        "input_field_names": ["lexical_text"],
        "output_field_names": ["bm25_sparse"],
    }
    sparse_index = next(
        item for item in create["index_params"].indexes
        if item["field_name"] == "bm25_sparse"
    )
    assert sparse_index["index_type"] == "SPARSE_INVERTED_INDEX"
    assert sparse_index["metric_type"] == "BM25"
    assert sparse_index["params"]["inverted_index_algo"] == "DAAT_MAXSCORE"
    assert "bm25_sparse" not in client.inserted[0]
    assert client.inserted[0]["lexical_text"].startswith("认证手册")


@pytest.mark.parametrize(
    "description",
    [
        {"fields": [{"name": "embedding", "type": "FLOAT_VECTOR", "params": {"dim": 3}}]},
        v2_description(3, include_function=False),
    ],
)
def test_existing_v1_or_incomplete_schema_is_rejected(
    tmp_path: Path,
    description: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingMilvusClient(dimensions=3)
    monkeypatch.setattr(client, "describe_collection", lambda **_: description)
    vectors = MilvusVectorStore(settings_for(tmp_path), "test-repo", client=client)

    with pytest.raises(ValueError, match="进阶版 RAG Schema|lexical_bm25"):
        vectors.search(
            "ERR_AUTH_401",
            [1.0, 0.0, 0.0],
            2,
            mode="hybrid",
            dense_candidate_k=4,
            keyword_candidate_k=4,
            rrf_k=60,
            ready_document_ids=["doc-1"],
            embedding_provider="test",
            embedding_model="test-model",
        )


def test_status_does_not_mark_a_collection_with_missing_fields_as_v2_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    description = v2_description(3)
    description["fields"] = [
        field for field in description["fields"] if field["name"] != "embedding_model"
    ]
    client = RecordingMilvusClient(dimensions=3)
    monkeypatch.setattr(client, "describe_collection", lambda **_: description)
    vectors = MilvusVectorStore(settings_for(tmp_path), "test-repo", client=client)

    status = vectors.status()

    assert status["available"] is True
    assert status["schema_version"] is None
    assert status["dense_search"] is False
    assert status["bm25_search"] is False
    assert "需要重建" in status["detail"]


@pytest.mark.parametrize(
    ("field_name", "replacement", "message"),
    [
        ("id", {"name": "id", "type": "VARCHAR", "is_primary": False}, "不是主键"),
        ("position", {"name": "position", "type": "VARCHAR"}, "字段类型不正确"),
        ("embedding", {"name": "embedding", "type": "FLOAT_VECTOR", "params": {"dim": 0}}, "有效向量维度"),
    ],
)
def test_v2_schema_rejects_invalid_primary_key_types_and_dimensions(
    field_name: str,
    replacement: dict[str, Any],
    message: str,
) -> None:
    description = v2_description(3)
    description["fields"] = [
        replacement if field["name"] == field_name else field
        for field in description["fields"]
    ]

    with pytest.raises(ValueError, match=message):
        MilvusVectorStore._validate_v2_schema(description)


def test_status_requires_the_scalar_filter_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingMilvusClient(dimensions=3)
    monkeypatch.setattr(
        client,
        "list_indexes",
        lambda **_: ["embedding_cosine", "lexical_bm25_index"],
    )
    vectors = MilvusVectorStore(settings_for(tmp_path), "test-repo", client=client)

    status = vectors.status()

    assert status["schema_version"] is None
    assert status["dense_search"] is False
    assert status["bm25_search"] is False


def test_dense_search_only_uses_embedding_route(tmp_path: Path) -> None:
    client = RecordingMilvusClient(dimensions=3)
    vectors = MilvusVectorStore(settings_for(tmp_path), "test-repo", client=client)

    results = vectors.search(
        "ERR_AUTH_401",
        [1.0, 0.0, 0.0],
        2,
        mode="dense",
        dense_candidate_k=5,
        keyword_candidate_k=6,
        rrf_k=60,
        ready_document_ids=["doc-1"],
        embedding_provider="test",
        embedding_model="test-model",
    )

    assert [item.id for item in results] == ["dense-only", "both"]
    assert [call["anns_field"] for call in client.search_calls] == ["embedding"]
    assert results[0].trace["dense_score"] == pytest.approx(0.91)
    assert "fusion_score" not in results[0].trace


def test_hybrid_search_uses_bm25_and_rrf_with_the_same_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_milvus_sdk(monkeypatch)
    client = RecordingMilvusClient(dimensions=3)
    vectors = MilvusVectorStore(settings_for(tmp_path), "test-repo", client=client)

    results = vectors.search(
        "ERR_AUTH_401",
        [1.0, 0.0, 0.0],
        2,
        mode="hybrid",
        dense_candidate_k=5,
        keyword_candidate_k=6,
        rrf_k=77,
        ready_document_ids=["doc-1", "doc-2"],
        embedding_provider="test",
        embedding_model="test-model",
    )

    assert [item.id for item in results] == ["keyword-only", "both"]
    assert [call["anns_field"] for call in client.search_calls] == [
        "embedding",
        "bm25_sparse",
    ]
    hybrid_call = client.hybrid_calls[0]
    dense_request, keyword_request = hybrid_call["reqs"]
    assert dense_request.options["filter"] == keyword_request.options["filter"]
    assert dense_request.options["param"]["metric_type"] == "COSINE"
    assert keyword_request.options["param"]["metric_type"] == "BM25"
    assert hybrid_call["ranker"].k == 77
    assert results[0].trace["sources"] == ["bm25"]
    assert results[1].trace["sources"] == ["dense", "bm25"]
    assert results[0].trace["fusion_score"] == pytest.approx(0.032)


def test_parent_lookup_filters_by_document_and_orders_children(tmp_path: Path) -> None:
    client = RecordingMilvusClient(dimensions=3)
    client.parent_rows = [
        {"id": "c2", "position": 3, "content": "third", "metadata": {"child_index": 2}},
        {"id": "c1", "position": 2, "content": "second", "metadata": {"child_index": 1}},
    ]
    vectors = MilvusVectorStore(settings_for(tmp_path), "test-repo", client=client)

    rows = vectors.list_parent_chunks("doc-1", "parent-a")

    assert [row["id"] for row in rows] == ["c1", "c2"]
    expression = client.query_calls[0]["filter"]
    assert 'document_id == "doc-1"' in expression
    assert 'metadata["parent_id"] == "parent-a"' in expression


@pytest.mark.parametrize(
    ("base_url", "expected_path"),
    [
        ("https://rerank.test/compatible-api/v1", "/compatible-api/v1/reranks"),
        ("https://rerank.test/compatible-api/v1/reranks", "/compatible-api/v1/reranks"),
    ],
)
def test_qwen_reranker_calls_configurable_endpoint_and_reorders_results(
    tmp_path: Path,
    base_url: str,
    expected_path: str,
) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.96},
                    {"index": 0, "relevance_score": 0.41},
                ]
            },
        )

    settings = settings_for(
        tmp_path,
        rerank_base_url=base_url,
        rerank_api_key="secret",
    )
    candidates = [
        search_result("a", "一般说明"),
        search_result("b", "ERR_AUTH_401 后刷新一次令牌"),
    ]
    outcome = asyncio.run(
        Reranker(settings, transport=httpx.MockTransport(handler)).rerank(
            "ERR_AUTH_401 怎么处理？",
            candidates,
            2,
        )
    )

    assert [item.id for item in outcome.results] == ["b", "a"]
    assert outcome.trace["mode"] == "qwen_api"
    assert outcome.trace["applied"] is True
    assert outcome.trace["fallback"] is False
    assert outcome.results[0].trace["rerank_score"] == pytest.approx(0.96)
    assert requests[0].url.path == expected_path
    assert requests[0].headers["authorization"] == "Bearer secret"
    body = json.loads(requests[0].content)
    assert body["model"] == "qwen3-rerank"
    assert body["top_n"] == 2
    assert len(body["documents"]) == 2


def test_qwen_reranker_falls_back_without_changing_rrf_order(
    tmp_path: Path,
) -> None:
    async def incomplete(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"index": 1, "relevance_score": 0.9}]},
        )

    settings = settings_for(tmp_path, rerank_api_key="secret")
    candidates = [search_result("a", "first"), search_result("b", "second")]
    outcome = asyncio.run(
        Reranker(settings, transport=httpx.MockTransport(incomplete)).rerank(
            "query",
            candidates,
            2,
        )
    )

    assert [item.id for item in outcome.results] == ["a", "b"]
    assert outcome.trace["mode"] == "fallback_hybrid"
    assert outcome.trace["fallback"] is True
    assert outcome.trace["fallback_to"] == "hybrid_rrf"
    assert "期望 2 条，实际 1 条" in outcome.trace["reason"]
    assert all(item.trace["rerank_score"] is None for item in outcome.results)


def test_missing_qwen_key_and_heuristic_baseline_are_explicit(tmp_path: Path) -> None:
    candidates = [
        search_result("a", "一般说明"),
        search_result("b", "ERR_AUTH_401 后刷新一次令牌"),
    ]

    missing_key = asyncio.run(Reranker(settings_for(tmp_path)).rerank("query", candidates, 2))
    heuristic = asyncio.run(
        Reranker(settings_for(tmp_path, rerank_provider="heuristic")).rerank(
            "ERR_AUTH_401 刷新令牌",
            candidates,
            2,
        )
    )

    assert missing_key.trace["fallback"] is True
    assert missing_key.trace["fallback_to"] == "hybrid_rrf"
    assert missing_key.trace["reason"] == "RERANK_API_KEY 未配置"
    assert heuristic.trace["mode"] == "heuristic_baseline"
    assert heuristic.trace["baseline"] is True
    assert heuristic.results[0].id == "b"
    assert heuristic.results[0].trace["rerank_mode"] == "heuristic_baseline"


class PipelineStore:
    def __init__(
        self,
        candidates: list[SearchResult],
        parent_rows: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.candidates = candidates
        self.parent_rows = parent_rows or {}
        self.search_calls: list[dict[str, Any]] = []
        self.parent_calls: list[tuple[str, str]] = []

    def embedding_fingerprints(self) -> set[tuple[str, str, int]]:
        return set()

    def search(self, query_text: str, query_vector: list[float], top_k: int, **options: Any) -> list[SearchResult]:
        self.search_calls.append({
            "query_text": query_text,
            "query_vector": query_vector,
            "top_k": top_k,
            **options,
        })
        return self.candidates[:top_k]

    def list_parent_chunks(self, document_id: str, parent_id: str) -> list[dict[str, Any]]:
        self.parent_calls.append((document_id, parent_id))
        return self.parent_rows.get((document_id, parent_id), [])


@pytest.mark.parametrize(
    ("mode", "store_mode", "effective_mode", "rerank_requested"),
    [
        ("dense", "dense", "dense", False),
        ("hybrid", "hybrid", "hybrid", False),
        ("hybrid_rerank", "hybrid", "hybrid_heuristic", True),
    ],
)
def test_pipeline_exposes_three_retrieval_modes(
    tmp_path: Path,
    mode: str,
    store_mode: str,
    effective_mode: str,
    rerank_requested: bool,
) -> None:
    settings = settings_for(tmp_path, rerank_provider="heuristic")
    store = PipelineStore([search_result("a", "ERR_AUTH_401 刷新令牌")])
    pipeline = RAGPipeline(settings, store, ModelClient(settings))

    outcome = asyncio.run(pipeline.retrieve("ERR_AUTH_401", top_k=1, mode=mode))

    assert store.search_calls[0]["mode"] == store_mode
    assert outcome.trace["requested_mode"] == mode
    assert outcome.trace["effective_mode"] == effective_mode
    assert outcome.trace["rerank"]["requested"] is rerank_requested


def test_parent_context_is_expanded_after_rerank_then_compressed(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path, context_chunk_max_chars=120)
    metadata_a = {"parent_id": "parent-a", "child_index": 0}
    metadata_b = {"parent_id": "parent-a", "child_index": 1}
    metadata_c = {"parent_id": "parent-b", "child_index": 0}
    candidates = [
        search_result("c0", "部署前先检查。", chunk_index=1, metadata=metadata_a),
        search_result(
            "c1",
            "当生产错误率超过 2% 并持续 5 分钟时，必须回滚。",
            chunk_index=2,
            metadata=metadata_b,
        ),
        search_result(
            "d0",
            "数据库变更需要单独审批。",
            chunk_index=3,
            metadata=metadata_c,
        ),
    ]
    parent_rows = {
        ("doc-1", "parent-a"): [
            {"id": "c0", "position": 1, "content": candidates[0].content, "metadata": metadata_a},
            {"id": "c1", "position": 2, "content": candidates[1].content, "metadata": metadata_b},
            {
                "id": "c2",
                "position": 3,
                "content": "后续观察服务状态。" * 40,
                "metadata": {"parent_id": "parent-a", "child_index": 2},
            },
        ],
        ("doc-1", "parent-b"): [
            {"id": "d0", "position": 3, "content": candidates[2].content, "metadata": metadata_c},
        ],
    }
    store = PipelineStore(candidates, parent_rows)
    pipeline = RAGPipeline(settings, store, ModelClient(settings))

    outcome = asyncio.run(
        pipeline.retrieve("错误率超过 2% 时是否回滚？", top_k=2, mode="hybrid")
    )

    assert len(outcome.results) == 2
    assert [item.id for item in outcome.results] == ["c0", "d0"]
    assert store.parent_calls == [
        ("doc-1", "parent-a"),
        ("doc-1", "parent-b"),
    ]
    assert "2%" in (outcome.results[0].context_content or "")
    assert outcome.trace["parent_deduplication"] == {
        "stage": "after_retrieval_before_rerank",
        "input_count": 3,
        "output_count": 2,
        "removed_count": 1,
        "parent_group_count": 2,
    }
    assert outcome.trace["parent_expansion"] == {
        "stage": "after_rerank_before_compression",
        "candidate_count": 2,
        "attempted_count": 2,
        "expanded_count": 1,
        "fallback_count": 0,
        "unique_parent_count": 2,
    }
    first_trace = outcome.results[0].trace
    assert first_trace["parent_deduplication"]["removed_lower_ranked_siblings"] == 1
    assert first_trace["parent_expansion"]["added_siblings"] == 2
    assert first_trace["compression"]["applied"] is True
    assert first_trace["compression"]["original_chars"] == first_trace["parent_expansion"]["expanded_chars"]


def test_missing_parent_metadata_keeps_the_original_candidate(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    candidate = search_result("legacy", "旧切片仍然可以检索", metadata={})
    store = PipelineStore([candidate])
    pipeline = RAGPipeline(settings, store, ModelClient(settings))

    outcome = asyncio.run(pipeline.retrieve("旧切片", top_k=1, mode="dense"))

    assert outcome.results[0].context_content == candidate.content
    expansion = outcome.results[0].trace["parent_expansion"]
    assert expansion["attempted"] is False
    assert expansion["reason"] == "parent_id_missing"
    assert store.parent_calls == []


def test_answer_gate_uses_route_evidence_not_the_small_rrf_score(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, rerank_min_score=0.15)
    pipeline = RAGPipeline(settings, PipelineStore([]), ModelClient(settings))
    hybrid = search_result("hybrid", "证据", score=0.031)
    low_qwen = replace(
        hybrid,
        trace={**hybrid.trace, "rerank_mode": "qwen_api", "rerank_score": 0.1},
    )

    assert pipeline._passes_answer_gate(hybrid) is True
    assert pipeline._passes_answer_gate(low_qwen) is False


def test_fallback_hybrid_rejects_a_weak_bm25_negative_question(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    weak_match = search_result(
        "weak",
        "公司的营收由财务部门汇总，并在年度报告中披露。",
        score=0.031,
        metadata={},
        trace={
            "retrieval_mode": "hybrid",
            "sources": ["dense", "bm25"],
            "dense_rank": 2,
            "dense_score": 0.42,
            "bm25_rank": 1,
            "bm25_score": 0.2,
            "fusion_method": "rrf",
            "fusion_score": 0.031,
        },
    )
    pipeline = RAGPipeline(
        settings,
        PipelineStore([weak_match]),
        ModelClient(settings),
    )

    answer = asyncio.run(
        pipeline.ask("公司的 2028 年营收目标是多少？", mode="hybrid_rerank")
    )

    assert answer["generation_mode"] == "no_evidence"
    assert answer["sources"] == []
    assert answer["retrieval"]["rerank"]["mode"] == "fallback_hybrid"
    assert answer["retrieval"]["rerank"]["fallback"] is True
    decision = answer["retrieval"]["answer_gate"]["decisions"][0]
    assert decision["accepted"] is True
    assert decision["reason"] == "dense_score"
    assert decision["missing_query_markers"] == ["2028"]
    union_gate = answer["retrieval"]["answer_gate"]["union_marker_gate"]
    assert union_gate["accepted"] is False
    assert union_gate["reason"] == "query_markers_missing_from_evidence_union"


def test_union_marker_gate_accepts_complementary_multi_source_evidence(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    candidates = [
        search_result("hotel", "上海普通岗位住宿上限为 600 元。", metadata={}),
        search_result("approval", "超过 5000 元由部门负责人二次审批。", metadata={}),
    ]
    pipeline = RAGPipeline(
        settings,
        PipelineStore(candidates),
        ModelClient(settings),
    )

    answer = asyncio.run(
        pipeline.ask(
            "上海住宿上限 600 元是否正确？超过 5000 元由谁审批？",
            top_k=2,
            mode="hybrid",
        )
    )

    assert answer["generation_mode"] == "extractive"
    assert [item["id"] for item in answer["sources"]] == ["hotel", "approval"]
    decisions = answer["retrieval"]["answer_gate"]["decisions"]
    assert decisions[0]["missing_query_markers"] == ["5000"]
    assert decisions[1]["missing_query_markers"] == ["600"]
    union_gate = answer["retrieval"]["answer_gate"]["union_marker_gate"]
    assert union_gate["accepted"] is True
    assert union_gate["missing_markers"] == []


def test_context_compression_honors_limits_above_600_characters(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, context_chunk_max_chars=800)
    pipeline = RAGPipeline(settings, PipelineStore([]), ModelClient(settings))
    candidate = search_result("long", "回滚条件：" + "A" * 1200, metadata={})

    compressed = pipeline._compress_results("回滚条件", [candidate])[0]

    assert len(compressed.context_content or "") == 800
    assert compressed.trace["compression"]["context_chars"] == 800


class CatalogOnlyVectorStore:
    def close(self) -> None:
        pass


def test_sqlite_only_stores_document_catalog_not_vectors(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    RAGStore(settings, vector_store=CatalogOnlyVectorStore())

    with sqlite3.connect(settings.catalog_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("documents")')
        }

    assert tables == {"documents"}
    assert "embedding" not in columns
    assert "content" not in columns


class IngestStore:
    def __init__(self, tmp_path: Path) -> None:
        self.document = DocumentRecord(
            id="doc-ingest",
            name="guide.md",
            stored_path=str(tmp_path / "guide.md"),
            content_type="text/markdown",
            checksum="checksum",
            file_size=1,
            character_count=0,
            chunk_count=0,
            status="processing",
            error_message=None,
            embedding_provider=None,
            embedding_model=None,
            embedding_dimensions=None,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        self.chunks: list[ChunkPayload] = []

    def get_by_checksum(self, _: str) -> None:
        return None

    def create_or_reset_document(self, **options: Any) -> tuple[DocumentRecord, bool]:
        self.document = replace(
            self.document,
            name=options["name"],
            stored_path=options["stored_path"],
            checksum=options["checksum"],
            file_size=options["file_size"],
        )
        return self.document, True

    def replace_chunks(self, *, chunks: list[ChunkPayload], character_count: int, **_: Any) -> DocumentRecord:
        self.chunks = chunks
        self.document = replace(
            self.document,
            character_count=character_count,
            chunk_count=len(chunks),
            status="ready",
            embedding_provider="deterministic",
            embedding_model="deterministic-hash-v1",
            embedding_dimensions=384,
        )
        return self.document

    def mark_failed(self, *_: Any) -> None:
        raise AssertionError("ingest should not fail")


def test_ingest_persists_parent_and_child_metadata(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    store = IngestStore(tmp_path)
    pipeline = RAGPipeline(settings, store, ModelClient(settings))
    body = (
        "# 发布策略\n\n"
        + "部署前检查健康状态。" * 35
        + "\n\n"
        + "错误率超过 2% 时回滚。" * 20
    ).encode("utf-8")

    document, created = asyncio.run(
        pipeline.ingest_bytes(
            filename="guide.md",
            data=body,
            content_type="text/markdown",
        )
    )

    assert created is True
    assert document.status == "ready"
    assert len(store.chunks) > 1
    assert all(chunk.metadata["parent_id"] for chunk in store.chunks)
    assert all("child_index" in chunk.metadata for chunk in store.chunks)
    assert all(chunk.metadata["chunk_id"] == chunk.id for chunk in store.chunks)
    assert all(chunk.lexical_text.startswith("发布策略\nguide.md") for chunk in store.chunks)
