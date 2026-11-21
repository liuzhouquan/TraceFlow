from __future__ import annotations

import argparse
from collections import Counter
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx


ROOT = Path(__file__).resolve().parents[1]
MODES = ("dense", "hybrid", "hybrid_rerank")


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
            raise RuntimeError(
                "release job failed: "
                f"{detail.get('error_message') or detail.get('message')}"
            )
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
            CorpusProblem("unexpected", f"存在冒烟集外资料：{', '.join(unexpected)}")
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


def normalized(value: str) -> str:
    return re.sub(r"[\s,，。；;：:]", "", value)


def trace_is_valid(mode: str, payload: dict[str, Any]) -> bool:
    trace = payload.get("retrieval") or {}
    if trace.get("requested_mode") != mode:
        return False
    rows = payload.get("results") or payload.get("sources") or []
    for row in rows:
        item = row.get("retrieval") or {}
        if mode == "dense" and item.get("dense_rank") is None:
            return False
        if mode != "dense" and (
            item.get("fusion_method") != "rrf"
            or item.get("fusion_score") is None
        ):
            return False
        if mode == "hybrid_rerank" and item.get("rerank_mode") is None:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test DevFlow Production RAG Studio")
    parser.add_argument("--base-url", default="http://127.0.0.1:8077")
    parser.add_argument("--repo-id", default="devflow-ai")
    parser.add_argument("--ask", action="store_true", help="also verify grounded answers")
    parser.add_argument("--require-real-rerank", action="store_true")
    parser.add_argument("--allow-extra-documents", action="store_true")
    args = parser.parse_args()

    expectations = json.loads(
        (ROOT / "sample_docs" / "expected_answers.json").read_text(encoding="utf-8")
    )
    failures: list[str] = []
    with httpx.Client(base_url=args.base_url, timeout=180.0) as client:
        repositories = request(client, "GET", "/api/repositories")
        if args.repo_id not in {item["id"] for item in repositories}:
            raise RuntimeError(f"unknown repo_id: {args.repo_id}")
        prefix = f"/api/repositories/{args.repo_id}"
        seeded = request(client, "POST", f"{prefix}/seed")
        job = ensure_active_release(client, prefix, "chapter-09-smoke-v1")
        corpus_problems = validate_sample_corpus(
            request(client, "GET", f"{prefix}/documents")
        )
        blocking_problems = blocking_corpus_problems(
            corpus_problems,
            allow_extra_documents=args.allow_extra_documents,
        )
        if blocking_problems:
            raise RuntimeError(
                "冒烟语料不是独立的五份示例资料。请使用独立的 CATALOG_PATH、"
                "repo_id、MILVUS_COLLECTION 和 MILVUS_COLLECTION_PREFIX "
                "启动测试实例；详情："
                + "；".join(problem.message for problem in blocking_problems)
            )
        status = request(client, "GET", f"{prefix}/status")
        vector = status.get("vector_store") or {}
        schema_ready = all(
            (
                vector.get("available"),
                vector.get("backend") == "Milvus",
                vector.get("schema_version") == "production-rag-v3",
                vector.get("dense_search"),
                vector.get("bm25_search"),
                vector.get("alias_target"),
            )
        )
        active = (status.get("release") or {}).get("active") or {}
        print(
            f"seed imported={seeded['imported']} duplicates={seeded['duplicates']} "
            f"job={job['state']} schema={'PASS' if schema_ready else 'FAIL'} "
            f"active={active.get('physical_collection') or '-'}"
        )
        if not schema_ready:
            failures.append(f"Milvus engineering alias/schema: {vector.get('detail')}")

        reconciliation = request(client, "POST", f"{prefix}/reconcile")
        print(
            f"reconcile={'PASS' if reconciliation.get('clean') else 'FAIL'} "
            f"target={reconciliation.get('alias_target') or '-'}"
        )
        if not reconciliation.get("clean"):
            failures.append("发布清单与 Milvus Active Collection 对账不一致")

        positives = [case for case in expectations if case["expected_document"]]
        for case in positives:
            for mode in MODES:
                result = request(
                    client,
                    "POST",
                    f"{prefix}/search",
                    json={"query": case["question"], "top_k": 4, "mode": mode},
                )
                filenames = [row.get("filename") for row in result.get("results") or []]
                found = case["expected_document"] in filenames
                trace_ok = trace_is_valid(mode, result)
                passed = found and trace_ok
                print(
                    f"{'PASS' if passed else 'FAIL'} {mode:<15} "
                    f"{case['expected_document']} rank="
                    f"{filenames.index(case['expected_document']) + 1 if found else '-'}"
                )
                if not passed:
                    failures.append(f"{mode}: {case['question']}")

        probe = request(
            client,
            "POST",
            f"{prefix}/search",
            json={
                "query": "REFRESH_TOKEN_EXPIRED 应该怎样处理？",
                "top_k": 4,
                "mode": "hybrid_rerank",
            },
        )
        rerank_mode = ((probe.get("retrieval") or {}).get("rerank") or {}).get("mode")
        print(f"effective rerank={rerank_mode or 'unknown'}")
        if args.require_real_rerank and rerank_mode != "qwen_api":
            failures.append("真实 Qwen Rerank 未生效")

        if args.ask:
            for case in expectations:
                result = request(
                    client,
                    "POST",
                    f"{prefix}/ask",
                    json={
                        "question": case["question"],
                        "top_k": 4,
                        "mode": "hybrid_rerank",
                    },
                )
                if not case["expected_document"]:
                    passed = (
                        result.get("generation_mode") == "no_evidence"
                        and not result.get("sources")
                    )
                else:
                    citations = [
                        int(value)
                        for value in re.findall(r"\[(\d+)\]", result.get("answer") or "")
                    ]
                    sources = result.get("sources") or []
                    citations_valid = bool(citations) and all(
                        1 <= value <= len(sources) for value in citations
                    )
                    answer = normalized(result.get("answer") or "")
                    keywords_present = all(
                        normalized(keyword) in answer for keyword in case["answer_keywords"]
                    )
                    passed = citations_valid and keywords_present
                print(
                    f"{'PASS' if passed else 'FAIL'} ask "
                    f"mode={result.get('generation_mode')} sources={len(result.get('sources') or [])}"
                )
                if not passed:
                    failures.append(f"ask: {case['question']}")

        investigation = request(
            client,
            "POST",
            f"{prefix}/investigate",
            json={
                "question": "新旧发布资料中的核心接口错误率回滚阈值是否冲突？",
                "contract": "release_conflict",
                "mode": "hybrid",
            },
        )
        conflict_detected = (
            investigation.get("decision") == "evidence_conflict"
            and (investigation.get("conflict") or {}).get("detected") is True
        )
        print(
            f"{'PASS' if conflict_detected else 'FAIL'} investigation "
            f"decision={investigation.get('decision')} "
            f"queries={(investigation.get('budgets') or {}).get('queries_used')}"
        )
        if not conflict_detected:
            failures.append("有限补证据工作流没有识别 2% 与 5% 的版本冲突")

    if failures:
        print("Smoke test failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
