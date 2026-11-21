from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import httpx


ROOT = Path(__file__).resolve().parents[1]
MODES = ("dense", "hybrid", "hybrid_rerank")


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    mode: str
    recall: float
    reciprocal_rank: float
    ndcg: float
    retrieved: list[str]
    relevant: list[str]
    rerank_mode: str | None


@dataclass(frozen=True)
class CorpusProblem:
    kind: Literal["missing", "unexpected", "duplicate", "not_ready"]
    message: str


def request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    response = client.request(method, path, **kwargs)
    if response.is_error:
        raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text}")
    return response.json()


def ensure_active_release(
    client: httpx.Client,
    prefix: str,
    idempotency_key: str,
) -> dict[str, Any]:
    job = request(
        client,
        "POST",
        f"{prefix}/releases/build",
        json={"idempotency_key": idempotency_key, "auto_promote": True},
    )
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        detail = request(client, "GET", f"{prefix}/jobs/{job['id']}")
        if detail["state"] == "succeeded":
            return detail
        if detail["state"] == "failed":
            raise RuntimeError(f"release job failed: {detail.get('error_message')}")
        time.sleep(0.25)
    raise RuntimeError("release job did not finish within 300 seconds")


def validate_sample_corpus(documents: list[dict[str, Any]]) -> list[CorpusProblem]:
    expected = {path.name for path in (ROOT / "sample_docs").glob("[0-9][0-9]_*.md")}
    names = [str(item.get("name") or "") for item in documents]
    counts = Counter(names)
    actual = set(names)
    problems: list[CorpusProblem] = []
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    duplicates = sorted(name for name, count in counts.items() if name and count > 1)
    not_ready = sorted(
        str(item.get("name") or "")
        for item in documents
        if item.get("name") in expected and item.get("status") != "ready"
    )
    if missing:
        problems.append(CorpusProblem("missing", f"缺少示例资料：{', '.join(missing)}"))
    if unexpected:
        problems.append(
            CorpusProblem("unexpected", f"存在评测集外资料：{', '.join(unexpected)}")
        )
    if duplicates:
        problems.append(
            CorpusProblem("duplicate", f"存在同名重复资料：{', '.join(duplicates)}")
        )
    if not_ready:
        problems.append(
            CorpusProblem("not_ready", f"示例资料未就绪：{', '.join(not_ready)}")
        )
    return problems


def blocking_corpus_problems(
    problems: list[CorpusProblem],
    *,
    allow_extra_documents: bool,
) -> list[CorpusProblem]:
    return [
        problem
        for problem in problems
        if not (allow_extra_documents and problem.kind == "unexpected")
    ]


def score_case(
    *,
    case_id: str,
    mode: str,
    relevant: list[str],
    retrieved: list[str],
    top_k: int,
    rerank_mode: str | None,
) -> CaseScore:
    if top_k < 1:
        raise ValueError("top_k 必须大于等于 1")
    relevant_set = set(relevant)
    ranked_documents = list(dict.fromkeys(retrieved))[:top_k]
    hits = [1 if filename in relevant_set else 0 for filename in ranked_documents]
    found = relevant_set.intersection(ranked_documents)
    recall = len(found) / max(1, len(relevant_set))
    reciprocal_rank = next(
        (1.0 / rank for rank, hit in enumerate(hits, start=1) if hit),
        0.0,
    )
    dcg = sum(hit / math.log2(rank + 1) for rank, hit in enumerate(hits, start=1))
    ideal_hits = min(len(relevant_set), top_k)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    ndcg = dcg / ideal_dcg if ideal_dcg else 0.0
    return CaseScore(
        case_id=case_id,
        mode=mode,
        recall=recall,
        reciprocal_rank=reciprocal_rank,
        ndcg=ndcg,
        retrieved=retrieved,
        relevant=relevant,
        rerank_mode=rerank_mode,
    )


def validate_trace(mode: str, payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    root_trace = payload.get("retrieval") or {}
    if root_trace.get("requested_mode") != mode:
        errors.append(f"requested_mode={root_trace.get('requested_mode')!r}")
    rerank_mode = (root_trace.get("rerank") or {}).get("mode")
    if mode == "hybrid_rerank" and rerank_mode not in {
        "qwen_api",
        "fallback_hybrid",
        "heuristic_baseline",
        "disabled",
        "not_run",
    }:
        errors.append(f"rerank.mode={rerank_mode!r}")

    for rank, result in enumerate(payload.get("results") or [], start=1):
        trace = result.get("retrieval") or {}
        if mode == "dense" and trace.get("dense_rank") is None:
            errors.append(f"result#{rank} missing dense_rank")
        if mode != "dense":
            if trace.get("fusion_method") != "rrf" or trace.get("fusion_score") is None:
                errors.append(f"result#{rank} missing RRF trace")
            if trace.get("dense_rank") is None and trace.get("bm25_rank") is None:
                errors.append(f"result#{rank} missing both route ranks")
        if mode == "hybrid_rerank" and trace.get("rerank_mode") is None:
            errors.append(f"result#{rank} missing rerank_mode")
    return errors


def aggregate(rows: list[CaseScore]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for mode in MODES:
        values = [row for row in rows if row.mode == mode]
        count = max(1, len(values))
        summary[mode] = {
            "Recall@K": sum(row.recall for row in values) / count,
            "MRR": sum(row.reciprocal_rank for row in values) / count,
            "nDCG@K": sum(row.ndcg for row in values) / count,
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Dense, Hybrid and Hybrid + Rerank on one fixed query set"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8077")
    parser.add_argument("--repo-id", default="isolation-lab")
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--skip-seed", action="store_true")
    parser.add_argument(
        "--allow-extra-documents",
        action="store_true",
        help="allow user documents to affect the fixed evaluation corpus",
    )
    parser.add_argument("--min-hybrid-recall", type=float, default=0.75)
    parser.add_argument("--require-real-rerank", action="store_true")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    cases = json.loads(
        (ROOT / "sample_docs" / "retrieval_cases.json").read_text(encoding="utf-8")
    )
    scores: list[CaseScore] = []
    trace_errors: list[str] = []

    with httpx.Client(base_url=args.base_url, timeout=180.0) as client:
        repositories = request(client, "GET", "/api/repositories")
        if args.repo_id not in {item["id"] for item in repositories}:
            raise RuntimeError(f"unknown repo_id: {args.repo_id}")
        prefix = f"/api/repositories/{args.repo_id}"
        status = request(client, "GET", f"{prefix}/status")
        vector = status.get("vector_store") or {}
        if vector.get("backend") != "Milvus" or not vector.get("available"):
            raise RuntimeError(f"Milvus is not ready: {vector.get('detail')}")
        if not args.skip_seed:
            request(client, "POST", f"{prefix}/seed")
            ensure_active_release(client, prefix, "chapter-09-evaluation-v1")
        corpus_problems = validate_sample_corpus(
            request(client, "GET", f"{prefix}/documents")
        )
        blocking_problems = blocking_corpus_problems(
            corpus_problems,
            allow_extra_documents=args.allow_extra_documents,
        )
        if blocking_problems:
            raise RuntimeError(
                "评测语料不是独立的五份示例资料。请使用独立的 CATALOG_PATH、"
                "repo_id 和 MILVUS_COLLECTION 启动评测实例；详情："
                + "；".join(problem.message for problem in blocking_problems)
            )

        for case in cases:
            for mode in MODES:
                payload = request(
                    client,
                    "POST",
                    f"{prefix}/search",
                    json={"query": case["query"], "top_k": args.top_k, "mode": mode},
                )
                results = payload.get("results") or []
                retrieved = [str(item.get("filename") or "") for item in results]
                rerank_mode = ((payload.get("retrieval") or {}).get("rerank") or {}).get(
                    "mode"
                )
                scores.append(
                    score_case(
                        case_id=case["id"],
                        mode=mode,
                        relevant=list(case["relevant_documents"]),
                        retrieved=retrieved,
                        top_k=args.top_k,
                        rerank_mode=rerank_mode,
                    )
                )
                trace_errors.extend(
                    f"{case['id']} [{mode}]: {message}"
                    for message in validate_trace(mode, payload)
                )

    summary = aggregate(scores)
    print(f"cases={len(cases)} top_k={args.top_k}")
    print("mode              Recall@K    MRR    nDCG@K")
    print("----------------  --------  -----  -------")
    for mode in MODES:
        values = summary[mode]
        print(
            f"{mode:<16}  {values['Recall@K']:.3f}     "
            f"{values['MRR']:.3f}  {values['nDCG@K']:.3f}"
        )

    rerank_modes = sorted(
        {row.rerank_mode for row in scores if row.mode == "hybrid_rerank" and row.rerank_mode}
    )
    print(f"hybrid_rerank effective modes: {', '.join(rerank_modes) or 'unknown'}")

    report = {
        "top_k": args.top_k,
        "summary": summary,
        "cases": [asdict(row) for row in scores],
        "trace_errors": trace_errors,
        "rerank_modes": rerank_modes,
        "corpus_warnings": [asdict(problem) for problem in corpus_problems],
    }
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    failures = list(trace_errors)
    for mode in ("hybrid", "hybrid_rerank"):
        if summary[mode]["Recall@K"] < args.min_hybrid_recall:
            failures.append(
                f"{mode} Recall@K {summary[mode]['Recall@K']:.3f} "
                f"< {args.min_hybrid_recall:.3f}"
            )
    if args.require_real_rerank and rerank_modes != ["qwen_api"]:
        failures.append(
            "真实 Rerank 未生效；请配置 RERANK_API_KEY，并确认结果为 qwen_api"
        )
    if failures:
        print("Evaluation failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Evaluation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
