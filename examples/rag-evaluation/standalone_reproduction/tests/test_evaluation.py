from __future__ import annotations

import math

import pytest

from scripts.evaluate_retrieval import (
    blocking_corpus_problems as blocking_evaluation_corpus_problems,
    score_case,
    validate_sample_corpus as validate_evaluation_corpus,
    validate_trace,
)
from scripts.smoke_test import (
    blocking_corpus_problems as blocking_smoke_corpus_problems,
    validate_sample_corpus as validate_smoke_corpus,
)


def test_document_metrics_count_each_retrieved_document_once() -> None:
    score = score_case(
        case_id="duplicate-chunks",
        mode="hybrid",
        relevant=["a.md"],
        retrieved=["a.md", "a.md", "a.md", "a.md"],
        top_k=4,
        rerank_mode=None,
    )

    assert score.recall == 1.0
    assert score.reciprocal_rank == 1.0
    assert score.ndcg == 1.0


def test_document_metrics_keep_first_distinct_document_order() -> None:
    score = score_case(
        case_id="two-documents",
        mode="hybrid",
        relevant=["a.md", "b.md"],
        retrieved=["noise.md", "noise.md", "a.md", "b.md"],
        top_k=4,
        rerank_mode=None,
    )

    assert score.recall == 1.0
    assert score.reciprocal_rank == pytest.approx(0.5)
    assert 0.0 <= score.ndcg <= 1.0


def test_ndcg_ideal_dcg_uses_requested_k_when_results_are_short() -> None:
    score = score_case(
        case_id="missing-relevant-document",
        mode="hybrid",
        relevant=["a.md", "b.md"],
        retrieved=["a.md"],
        top_k=4,
        rerank_mode=None,
    )

    assert score.recall == 0.5
    assert score.ndcg == pytest.approx(1.0 / (1.0 + 1.0 / math.log2(3)))


@pytest.mark.parametrize("rerank_mode", ["disabled", "not_run"])
def test_trace_validation_accepts_explicit_non_model_rerank_states(
    rerank_mode: str,
) -> None:
    payload = {
        "retrieval": {
            "requested_mode": "hybrid_rerank",
            "rerank": {"mode": rerank_mode},
        },
        "results": [],
    }

    assert validate_trace("hybrid_rerank", payload) == []


@pytest.mark.parametrize(
    "validator",
    [validate_evaluation_corpus, validate_smoke_corpus],
)
def test_quality_scripts_reject_documents_outside_the_fixed_corpus(validator) -> None:
    documents = [
        {"name": "01_账号与令牌故障排查.md", "status": "ready"},
        {"name": "02_生产发布与回滚手册.md", "status": "ready"},
        {"name": "03_差旅与报销制度.md", "status": "ready"},
        {"name": "04_移动端登录体验调研.md", "status": "ready"},
        {"name": "用户上传.md", "status": "ready"},
    ]

    problems = validator(documents)

    assert any("用户上传.md" in problem.message for problem in problems)


@pytest.mark.parametrize(
    "validator",
    [validate_evaluation_corpus, validate_smoke_corpus],
)
def test_quality_scripts_reject_duplicate_sample_names(validator) -> None:
    documents = [
        {"name": "01_账号与令牌故障排查.md", "status": "ready"},
        {"name": "02_生产发布与回滚手册.md", "status": "ready"},
        {"name": "03_差旅与报销制度.md", "status": "ready"},
        {"name": "04_移动端登录体验调研.md", "status": "ready"},
        {"name": "01_账号与令牌故障排查.md", "status": "ready"},
    ]

    problems = validator(documents)

    assert any("同名重复" in problem.message for problem in problems)


@pytest.mark.parametrize(
    ("validator", "blocker"),
    [
        (validate_evaluation_corpus, blocking_evaluation_corpus_problems),
        (validate_smoke_corpus, blocking_smoke_corpus_problems),
    ],
)
def test_allow_extra_documents_only_ignores_unexpected_files(validator, blocker) -> None:
    documents = [
        {"name": "01_账号与令牌故障排查.md", "status": "failed"},
        {"name": "02_生产发布与回滚手册.md", "status": "ready"},
        {"name": "03_差旅与报销制度.md", "status": "ready"},
        {"name": "用户上传.md", "status": "ready"},
        {"name": "02_生产发布与回滚手册.md", "status": "ready"},
    ]

    problems = validator(documents)
    blocking = blocker(problems, allow_extra_documents=True)

    assert {problem.kind for problem in problems} == {
        "missing",
        "unexpected",
        "duplicate",
        "not_ready",
    }
    assert {problem.kind for problem in blocking} == {
        "missing",
        "duplicate",
        "not_ready",
    }
