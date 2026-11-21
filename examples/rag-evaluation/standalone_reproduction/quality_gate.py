from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from config import Settings
from rag_pipeline import RAGPipeline, _query_markers


@dataclass(frozen=True)
class RetrievalScore:
    case_id: str
    kind: str
    recall: float
    reciprocal_rank: float
    ndcg: float
    retrieved: list[str]
    relevant: list[str]
    passed: bool
    detail: str


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"评测集第 {line_number} 行不是合法 JSON：{exc}") from exc
        if not isinstance(value, dict) or not value.get("id") or not value.get("query"):
            raise ValueError(f"评测集第 {line_number} 行缺少 id 或 query")
        cases.append(value)
    if not cases:
        raise ValueError("评测集不能为空")
    return cases


def _rank_metrics(relevant: list[str], retrieved: list[str], top_k: int) -> tuple[float, float, float]:
    relevant_set = set(relevant)
    ranked = list(dict.fromkeys(retrieved))[:top_k]
    hits = [1 if item in relevant_set else 0 for item in ranked]
    recall = len(relevant_set.intersection(ranked)) / max(1, len(relevant_set))
    reciprocal_rank = next(
        (1.0 / rank for rank, hit in enumerate(hits, start=1) if hit),
        0.0,
    )
    dcg = sum(hit / math.log2(rank + 1) for rank, hit in enumerate(hits, start=1))
    ideal_hits = min(len(relevant_set), top_k)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return recall, reciprocal_rank, dcg / ideal_dcg if ideal_dcg else 0.0


def _average(rows: list[RetrievalScore], field: str) -> float:
    if not rows:
        return 0.0
    return sum(float(getattr(row, field)) for row in rows) / len(rows)


async def evaluate_release(
    *,
    settings: Settings,
    pipeline: RAGPipeline,
    cases_path: Path,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    scores: list[RetrievalScore] = []
    negative_results: list[bool] = []
    conflict_results: list[bool] = []

    for case in cases:
        kind = str(case.get("kind") or "positive")
        relevant = [str(value) for value in case.get("relevant_documents") or []]
        query = str(case["query"])
        outcome = await pipeline.retrieve(
            query,
            settings.release_top_k,
            "hybrid",
        )
        retrieved = [result.filename for result in outcome.results]

        if kind == "no_answer":
            accepted = [
                result
                for result in outcome.results
                if pipeline._answer_gate_decision(result, query)[0]
            ]
            required_markers = _query_markers(query)
            union_evidence = "\n".join(
                result.context_content or result.content for result in accepted
            ).lower()
            missing_markers = sorted(
                marker for marker in required_markers if marker not in union_evidence
            )
            passed = not accepted or bool(missing_markers)
            negative_results.append(passed)
            scores.append(
                RetrievalScore(
                    case_id=str(case["id"]),
                    kind=kind,
                    recall=1.0 if passed else 0.0,
                    reciprocal_rank=1.0 if passed else 0.0,
                    ndcg=1.0 if passed else 0.0,
                    retrieved=retrieved,
                    relevant=[],
                    passed=passed,
                    detail=(
                        "answer_gate_rejected_all"
                        if not accepted
                        else "union_marker_gate_rejected_evidence"
                        if missing_markers
                        else "answer_gate_accepted_unrelated_evidence"
                    ),
                )
            )
            continue

        recall, reciprocal_rank, ndcg = _rank_metrics(
            relevant,
            retrieved,
            settings.release_top_k,
        )
        if kind == "conflict":
            evidence = "\n".join(
                result.context_content or result.content for result in outcome.results
            )
            alternatives = [str(value) for value in case.get("conflict_values") or []]
            conflict_found = len(alternatives) >= 2 and all(
                value in evidence for value in alternatives
            )
            passed = recall >= 1.0 and conflict_found
            conflict_results.append(passed)
            detail = "conflict_detected" if passed else "conflict_evidence_incomplete"
        else:
            passed = recall >= 1.0
            detail = "relevant_evidence_found" if passed else "relevant_evidence_missing"
        scores.append(
            RetrievalScore(
                case_id=str(case["id"]),
                kind=kind,
                recall=recall,
                reciprocal_rank=reciprocal_rank,
                ndcg=ndcg,
                retrieved=retrieved,
                relevant=relevant,
                passed=passed,
                detail=detail,
            )
        )

    positive = [row for row in scores if row.kind in {"positive", "conflict"}]
    metrics = {
        "recall_at_k": round(_average(positive, "recall"), 6),
        "mrr": round(_average(positive, "reciprocal_rank"), 6),
        "ndcg_at_k": round(_average(positive, "ndcg"), 6),
        "negative_rejection_rate": round(
            sum(negative_results) / len(negative_results) if negative_results else 1.0,
            6,
        ),
        "conflict_detection_rate": round(
            sum(conflict_results) / len(conflict_results) if conflict_results else 1.0,
            6,
        ),
    }
    thresholds = {
        "recall_at_k": settings.quality_recall_at_k_min,
        "mrr": settings.quality_mrr_min,
        "ndcg_at_k": settings.quality_ndcg_at_k_min,
        "negative_rejection_rate": settings.quality_negative_rejection_min,
        "conflict_detection_rate": 1.0,
    }
    checks = {
        name: metrics[name] >= minimum
        for name, minimum in thresholds.items()
    }
    dataset_sha256 = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    regressions: dict[str, dict[str, Any]] = {}
    baseline_compatible = bool(
        baseline
        and baseline.get("dataset_sha256") == dataset_sha256
        and baseline.get("top_k") == settings.release_top_k
        and baseline.get("retrieval_mode") == "hybrid"
    )
    baseline_metrics = (
        dict((baseline or {}).get("metrics") or {})
        if baseline_compatible
        else {}
    )
    for name in ("recall_at_k", "mrr", "ndcg_at_k", "negative_rejection_rate"):
        if name not in baseline_metrics:
            continue
        floor = float(baseline_metrics[name]) - settings.quality_regression_tolerance
        passed = metrics[name] >= floor
        regressions[name] = {
            "passed": passed,
            "baseline": float(baseline_metrics[name]),
            "candidate": metrics[name],
            "tolerance": settings.quality_regression_tolerance,
        }

    critical_cases_passed = all(row.passed for row in scores)
    passed = all(checks.values()) and all(
        item["passed"] for item in regressions.values()
    ) and critical_cases_passed
    return {
        "dataset": cases_path.name,
        "dataset_sha256": dataset_sha256,
        "case_count": len(scores),
        "top_k": settings.release_top_k,
        "retrieval_mode": "hybrid",
        "metrics": metrics,
        "thresholds": thresholds,
        "checks": checks,
        "regression_checks": regressions,
        "baseline_compatible": baseline_compatible,
        "critical_cases_passed": critical_cases_passed,
        "passed": passed,
        "cases": [asdict(row) for row in scores],
    }
