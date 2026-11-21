from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from pathlib import Path

import pytest

from config import Settings
from rag_store import ChunkPayload, MilvusVectorStore
from reranker import Reranker


pytestmark = pytest.mark.integration


def live_settings(tmp_path: Path) -> Settings:
    uri = os.getenv("MILVUS_TEST_URI", "").strip()
    if not uri:
        pytest.skip("set MILVUS_TEST_URI to run the live Milvus V3 test")
    suffix = uuid.uuid4().hex[:12]
    return Settings(
        provider="deterministic",
        base_url="http://127.0.0.1:11434",
        api_key="",
        embedding_model="integration-four-dimensional",
        chat_model="unused",
        knowledge_base_name="Milvus V3 integration test",
        chunk_size=220,
        chunk_overlap=40,
        top_k=2,
        min_relevance_score=0.35,
        max_upload_bytes=1024 * 1024,
        model_timeout_seconds=5,
        embedding_dimensions=4,
        embedding_batch_size=4,
        catalog_path=tmp_path / "catalog.sqlite3",
        upload_dir=tmp_path / "uploads",
        milvus_uri=uri,
        milvus_token=os.getenv("MILVUS_TEST_TOKEN", "").strip(),
        milvus_database=os.getenv("MILVUS_TEST_DATABASE", "default").strip()
        or "default",
        milvus_collection=f"advanced_rag_live_{suffix}",
        milvus_timeout_seconds=30,
        milvus_write_batch_size=4,
    )


def chunk(
    item_id: str,
    position: int,
    content: str,
    lexical_text: str,
    vector: list[float],
    *,
    parent_id: str,
    child_index: int,
) -> ChunkPayload:
    return ChunkPayload(
        id=item_id,
        position=position,
        content=content,
        lexical_text=lexical_text,
        vector=vector,
        metadata={
            "chunk_id": item_id,
            "parent_id": parent_id,
            "child_index": child_index,
            "section_path": ["故障处理"],
        },
    )


def test_live_milvus_v3_hybrid_alias_switch_and_rollback(tmp_path: Path) -> None:
    pytest.importorskip("pymilvus")
    settings = live_settings(tmp_path)
    repo_id = f"live-repo-{uuid.uuid4().hex[:12]}"
    vectors = MilvusVectorStore(settings, repo_id)
    second: MilvusVectorStore | None = None
    alias: str | None = None
    provider = "integration"
    model = "four-dimensional-test"

    try:
        vectors.replace_document(
            document_id="dense-doc",
            filename="deploy.md",
            title="部署手册",
            chunks=[
                chunk(
                    "dense-1",
                    1,
                    "部署前先检查服务健康状态。",
                    "部署手册 服务健康状态 发布检查",
                    [1.0, 0.0, 0.0, 0.0],
                    parent_id="deploy-parent",
                    child_index=0,
                ),
                chunk(
                    "dense-2",
                    2,
                    "部署后观察错误率五分钟。",
                    "部署手册 观察错误率 五分钟",
                    [0.95, 0.05, 0.0, 0.0],
                    parent_id="deploy-parent",
                    child_index=1,
                ),
            ],
            embedding_provider=provider,
            embedding_model=model,
        )
        vectors.replace_document(
            document_id="keyword-doc",
            filename="auth.md",
            title="认证故障手册",
            chunks=[
                chunk(
                    "keyword-1",
                    1,
                    "ERR_AUTH_401 出现后只刷新一次访问令牌。",
                    "认证故障手册 ERR_AUTH_401 ERR_AUTH_401 刷新令牌",
                    [0.0, 1.0, 0.0, 0.0],
                    parent_id="auth-parent",
                    child_index=0,
                )
            ],
            embedding_provider=provider,
            embedding_model=model,
        )
        vectors.replace_document(
            document_id="noise-doc",
            filename="expense.md",
            title="报销手册",
            chunks=[
                chunk(
                    "noise-1",
                    1,
                    "住宿费用需要上传发票。",
                    "报销手册 住宿费用 发票",
                    [0.0, 0.0, 1.0, 0.0],
                    parent_id="expense-parent",
                    child_index=0,
                )
            ],
            embedding_provider=provider,
            embedding_model=model,
        )

        status = vectors.status()
        assert status["available"] is True
        assert status["schema_version"] == "production-rag-v3"
        assert status["dense_search"] is True
        assert status["bm25_search"] is True

        description = vectors.client.describe_collection(
            collection_name=settings.milvus_collection
        )
        function_names = {
            item.get("name") or item.get("function_name")
            for item in description.get("functions") or []
        }
        assert "lexical_bm25" in function_names

        ready_ids = ["dense-doc", "keyword-doc", "noise-doc"]
        dense = vectors.search(
            "ERR_AUTH_401",
            [1.0, 0.0, 0.0, 0.0],
            2,
            mode="dense",
            dense_candidate_k=2,
            keyword_candidate_k=2,
            rrf_k=60,
            ready_document_ids=ready_ids,
            embedding_provider=provider,
            embedding_model=model,
        )
        hybrid = vectors.search(
            "ERR_AUTH_401",
            [1.0, 0.0, 0.0, 0.0],
            2,
            mode="hybrid",
            dense_candidate_k=2,
            keyword_candidate_k=2,
            rrf_k=60,
            ready_document_ids=ready_ids,
            embedding_provider=provider,
            embedding_model=model,
        )

        assert [item.id for item in dense] == ["dense-1", "dense-2"]
        assert "keyword-1" in {item.id for item in hybrid}
        keyword_result = next(item for item in hybrid if item.id == "keyword-1")
        assert keyword_result.trace["bm25_rank"] == 1
        assert keyword_result.trace["fusion_method"] == "rrf"
        assert 0.0 < keyword_result.trace["fusion_score"] < 0.1

        fallback = asyncio.run(
            Reranker(settings).rerank("ERR_AUTH_401", hybrid, top_k=2)
        )
        assert [item.id for item in fallback.results] == [item.id for item in hybrid]
        assert fallback.trace["mode"] == "fallback_hybrid"
        assert fallback.trace["fallback_to"] == "hybrid_rrf"

        parent = vectors.list_parent_chunks("dense-doc", "deploy-parent")
        assert [item["id"] for item in parent] == ["dense-1", "dense-2"]
        assert [item["metadata"]["child_index"] for item in parent] == [0, 1]

        alias = f"advanced_rag_alias_{repo_id[-12:]}"
        assert vectors.point_alias(alias, settings.milvus_collection) == settings.milvus_collection
        assert vectors.alias_target(alias) == settings.milvus_collection

        second_settings = Settings(
            **{
                **settings.__dict__,
                "milvus_collection": f"{settings.milvus_collection}_next",
            }
        )
        second = MilvusVectorStore(second_settings, repo_id, client=vectors.client)
        second.replace_document(
            document_id="next-doc",
            filename="next.md",
            title="下一版本",
            chunks=[
                chunk(
                    "next-1",
                    1,
                    "新版本候选已经完成验证。",
                    "新版本 候选 验证",
                    [0.0, 0.0, 0.0, 1.0],
                    parent_id="next-parent",
                    child_index=0,
                )
            ],
            embedding_provider=provider,
            embedding_model=model,
        )
        assert second.point_alias(alias, second_settings.milvus_collection) == second_settings.milvus_collection
        assert second.alias_target(alias) == second_settings.milvus_collection
        assert second.point_alias(alias, settings.milvus_collection) == settings.milvus_collection
        assert second.alias_target(alias) == settings.milvus_collection
        vectors.client.drop_alias(alias=alias)
        second.reset()

        vectors.delete_document("dense-doc")
        assert vectors.list_chunks("dense-doc") == []
        assert vectors.list_chunks("keyword-doc")[0]["id"] == "keyword-1"
    finally:
        if alias:
            with contextlib.suppress(Exception):
                vectors.client.drop_alias(alias=alias)
        if second is not None:
            with contextlib.suppress(Exception):
                second.reset()
        with contextlib.suppress(Exception):
            vectors.reset()
        with contextlib.suppress(Exception):
            vectors.close()
