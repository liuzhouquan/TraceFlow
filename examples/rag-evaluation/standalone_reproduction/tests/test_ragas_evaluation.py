from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from config import Settings
from ragas_evaluation import (
    EvaluationRunStore,
    GenerationEvalCase,
    evaluate_generation_quality,
    load_generation_cases,
)


def settings_for(tmp_path: Path, evaluation_set: Path) -> Settings:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        provider="deterministic",
        base_url="http://127.0.0.1:11434",
        api_key="",
        embedding_model="deterministic-hash-v1",
        chat_model="unused",
        knowledge_base_name="ragas-test",
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
        milvus_collection="ragas_test_active",
        milvus_timeout_seconds=5,
        milvus_write_batch_size=2,
        ragas_evaluation_set=evaluation_set,
    )


class FakeModels:
    embedding_provider = "fake-embedding"
    embedding_model = "fake-embedding-v1"


class FakePipeline:
    models = FakeModels()

    def __init__(self, answers: dict[str, dict[str, Any]]) -> None:
        self.answers = answers

    async def status(self) -> dict[str, Any]:
        return {
            "vector_store": {
                "alias": "ragas_test_active",
                "alias_target": "ragas_test_v001",
            },
            "generation": {"model": "fake-chat-v1"},
        }

    async def ask(self, question: str, top_k: int, mode: str) -> dict[str, Any]:
        assert top_k == 4
        assert mode == "hybrid"
        return self.answers[question]


class FakeScorer:
    version = "0.4.3-test"

    def __init__(self, values: dict[str, float] | None = None) -> None:
        self.values = values or {
            "context_precision": 0.95,
            "context_recall": 0.96,
            "faithfulness": 0.97,
            "answer_relevancy": 0.94,
        }

    async def score(self, **_: Any) -> dict[str, float]:
        return self.values


def _case(kind: str = "positive") -> GenerationEvalCase:
    if kind == "no_answer":
        return GenerationEvalCase(
            id="no-answer",
            kind=kind,
            query="没有答案的问题",
            reference="知识库没有答案，应明确拒答。",
            relevant_documents=(),
            must_include=("无法确定",),
            must_not_include=("100 亿元",),
        )
    return GenerationEvalCase(
        id="release-rate",
        kind=kind,
        query="什么时候回滚？",
        reference="错误率超过 2% 并持续 5 分钟时立即回滚。",
        relevant_documents=("02_生产发布与回滚手册.md",),
        must_include=("2%", "5 分钟"),
        must_not_include=("5%",),
    )


def _answer(text: str, filename: str | None) -> dict[str, Any]:
    sources = [] if filename is None else [{"id": "chunk-1", "filename": filename, "snippet": "错误率超过 2% 并持续 5 分钟时立即回滚。"}]
    return {
        "answer": text,
        "sources": sources,
        "generation_mode": "no_evidence" if not sources else "llm",
        "took_ms": 12,
    }


@pytest.mark.asyncio
async def test_generation_evaluation_combines_hard_rules_and_ragas(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text('{"id":"placeholder"}\n', encoding="utf-8")
    positive = _case()
    no_answer = _case("no_answer")
    pipeline = FakePipeline(
        {
            positive.query: _answer(
                "错误率超过 2% 并持续 5 分钟时立即回滚。[1]",
                "02_生产发布与回滚手册.md",
            ),
            no_answer.query: _answer("根据当前知识库，我无法确定。", None),
        }
    )

    result = await evaluate_generation_quality(
        settings=settings_for(tmp_path, dataset),
        pipeline=pipeline,
        run_judge=True,
        top_k=4,
        mode="hybrid",
        cases=[positive, no_answer],
        scorer=FakeScorer(),
    )

    assert result["status"] == "passed"
    assert result["hard_gate"] == {"passed": True, "passed_cases": 2, "total_cases": 2}
    assert result["ragas_gate"]["metrics"]["faithfulness"]["score"] == 0.97
    assert result["ragas_gate"]["metrics"]["faithfulness"]["scored_cases"] == 1
    assert result["cases"][1]["ragas_skipped_reason"] == "no_answer_uses_hard_rules"


@pytest.mark.asyncio
async def test_critical_number_rule_fails_even_when_ragas_scores_are_high(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text('{"id":"placeholder"}\n', encoding="utf-8")
    case = _case()
    pipeline = FakePipeline(
        {
            case.query: _answer(
                "错误率超过 5% 并持续 10 分钟时回滚。[1]",
                "02_生产发布与回滚手册.md",
            )
        }
    )

    result = await evaluate_generation_quality(
        settings=settings_for(tmp_path, dataset),
        pipeline=pipeline,
        run_judge=True,
        top_k=4,
        mode="hybrid",
        cases=[case],
        scorer=FakeScorer(),
    )

    assert result["status"] == "failed"
    assert result["hard_gate"]["passed"] is False
    assert result["cases"][0]["hard_rules"]["forbidden_phrases"] == ["5%"]


@pytest.mark.asyncio
async def test_judge_disabled_returns_partial_instead_of_fake_zero(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text('{"id":"placeholder"}\n', encoding="utf-8")
    case = _case()
    pipeline = FakePipeline(
        {
            case.query: _answer(
                "错误率超过 2% 并持续 5 分钟时立即回滚。[1]",
                "02_生产发布与回滚手册.md",
            )
        }
    )

    result = await evaluate_generation_quality(
        settings=settings_for(tmp_path, dataset),
        pipeline=pipeline,
        run_judge=False,
        top_k=4,
        mode="hybrid",
        cases=[case],
    )

    assert result["status"] == "partial"
    assert result["hard_gate"]["passed"] is True
    assert result["ragas_gate"]["metrics"]["faithfulness"]["score"] is None


@pytest.mark.asyncio
async def test_requested_but_unavailable_judge_is_operational_error(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text('{"id":"placeholder"}\n', encoding="utf-8")
    case = _case()
    pipeline = FakePipeline(
        {
            case.query: _answer(
                "错误率超过 2% 并持续 5 分钟时立即回滚。[1]",
                "02_生产发布与回滚手册.md",
            )
        }
    )

    result = await evaluate_generation_quality(
        settings=settings_for(tmp_path, dataset),
        pipeline=pipeline,
        run_judge=True,
        top_k=4,
        mode="hybrid",
        cases=[case],
    )

    assert result["status"] == "error"
    assert result["hard_gate"]["passed"] is True
    assert result["judge"]["available"] is False
    assert "deterministic Embedding" in result["judge"]["error"]


def test_run_store_persists_status_and_result(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text('{"id":"placeholder"}\n', encoding="utf-8")
    store = EvaluationRunStore(settings_for(tmp_path, dataset))

    created = store.create(run_judge=True, top_k=4, mode="hybrid")
    completed = store.update(
        created["run_id"],
        status="passed",
        result={"passed": True},
    )

    assert store.get(created["run_id"]) == completed
    assert store.list()[0]["status"] == "passed"


def test_course_evaluation_set_has_references_and_hard_rules() -> None:
    path = Path(__file__).resolve().parents[1] / "eval_cases" / "held_out_v1.jsonl"

    cases = load_generation_cases(path)

    assert len(cases) == 9
    assert {case.kind for case in cases} == {"positive", "no_answer", "conflict"}
    assert all(case.reference for case in cases)
    assert all(case.must_include for case in cases)

    by_id = {case.id: case for case in cases}
    assert by_id["auth-401-recovery"].must_not_include == ()
    assert by_id["auth-exact-error-code"].must_not_include == ()
    assert by_id["cross-document-login-release"].must_not_include == ()
    assert "清空本地登录态" in by_id["auth-401-recovery"].must_include
    assert "清空本地登录态" in by_id["cross-document-login-release"].must_include


def test_checked_in_calibration_report_is_explicitly_not_end_to_end() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "eval_results"
        / "ragas_calibration_2026-08-08.json"
    )

    report = json.loads(path.read_text(encoding="utf-8"))

    assert report["ragas_version"] == "0.4.3"
    assert "不是端到端" in report["purpose"]
    assert [row["id"] for row in report["rows"]] == [
        "good-grounded",
        "authority-conflict-wrong",
        "missing-evidence-vague-answer",
    ]
    assert report["rows"][0]["scores"]["answer_relevancy"] == 0.7655
