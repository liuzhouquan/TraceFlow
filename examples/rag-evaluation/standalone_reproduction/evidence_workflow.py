from __future__ import annotations

import time
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Any

from config import Settings
from rag_pipeline import RAGPipeline
from rag_store import SearchResult


@dataclass(frozen=True)
class EvidenceSlot:
    name: str
    query: str
    source_patterns: tuple[str, ...]
    required_markers: tuple[str, ...] = ()
    required: bool = True

    def accepts(self, result: SearchResult) -> bool:
        source_ok = not self.source_patterns or any(
            fnmatch(result.filename, pattern) for pattern in self.source_patterns
        )
        evidence = (result.context_content or result.content).lower()
        markers_ok = not self.required_markers or all(
            marker.lower() in evidence for marker in self.required_markers
        )
        return source_ok and markers_ok


@dataclass(frozen=True)
class EvidenceContract:
    name: str
    slots: tuple[EvidenceSlot, ...]
    conflict_values: tuple[str, ...] = ()


CONTRACTS: dict[str, EvidenceContract] = {
    "release_conflict": EvidenceContract(
        name="release_conflict",
        slots=(
            EvidenceSlot(
                name="current_policy",
                query="当前有效生产发布手册的核心接口错误率回滚阈值",
                source_patterns=("02_生产发布与回滚手册.md",),
                required_markers=("2%", "5 分钟"),
            ),
            EvidenceSlot(
                name="legacy_policy",
                query="已废弃旧版发布阈值备忘的错误率和持续时间",
                source_patterns=("05_已废弃的发布阈值备忘.md",),
                required_markers=("5%", "10 分钟"),
            ),
        ),
        conflict_values=("2%", "5%"),
    ),
    "login_release": EvidenceContract(
        name="login_release",
        slots=(
            EvidenceSlot(
                name="runtime_recovery",
                query="HTTP 401 后刷新 Access Token 并恢复原请求的步骤",
                source_patterns=("01_账号与令牌故障排查.md",),
                required_markers=("401",),
            ),
            EvidenceSlot(
                name="release_checklist",
                query="登录链路生产发布前需要验证的路径",
                source_patterns=("02_生产发布与回滚手册.md",),
                required_markers=("Access Token",),
            ),
        ),
    ),
}


def _public_evidence(result: SearchResult) -> dict[str, Any]:
    return {
        "id": result.id,
        "filename": result.filename,
        "title": result.title,
        "snippet": result.context_content or result.content,
        "score": round(result.score, 6),
        "retrieval": result.trace,
    }


async def investigate(
    *,
    settings: Settings,
    pipeline: RAGPipeline,
    question: str,
    contract_name: str,
    mode: str = "hybrid_rerank",
) -> dict[str, Any]:
    clean_question = question.strip()
    if not clean_question:
        raise ValueError("调查问题不能为空")
    contract = CONTRACTS.get(contract_name)
    if contract is None:
        raise ValueError(f"未知证据合同：{contract_name}")

    started = time.perf_counter()
    found: dict[str, SearchResult] = {}
    seen_ids: set[str] = set()
    trace: list[dict[str, Any]] = []
    queries = [clean_question]
    stop_reason = "query_budget_reached"

    for slot in contract.slots:
        if slot.query not in queries:
            queries.append(slot.query)
    queries = queries[: settings.investigation_max_queries]

    for round_index, query in enumerate(queries, start=1):
        if round_index > settings.investigation_max_rounds + 1:
            break
        outcome = await pipeline.retrieve(query, settings.release_top_k, mode)
        new_ids: list[str] = []
        for result in outcome.results:
            if result.id not in seen_ids:
                seen_ids.add(result.id)
                new_ids.append(result.id)
            for slot in contract.slots:
                if slot.name not in found and slot.accepts(result):
                    found[slot.name] = result
        missing = [
            slot.name for slot in contract.slots if slot.required and slot.name not in found
        ]
        trace.append(
            {
                "round": round_index,
                "query": query,
                "retrieved": [result.id for result in outcome.results],
                "new_evidence": new_ids,
                "found_slots": sorted(found),
                "missing_slots": missing,
                "retrieval": outcome.trace,
            }
        )
        if not missing:
            stop_reason = "evidence_contract_satisfied"
            break
        if round_index > 1 and not new_ids:
            stop_reason = "no_new_evidence"
            break

    missing = [
        slot.name for slot in contract.slots if slot.required and slot.name not in found
    ]
    combined = "\n".join(
        result.context_content or result.content for result in found.values()
    )
    conflict_detected = len(contract.conflict_values) >= 2 and all(
        value in combined for value in contract.conflict_values
    )
    if missing:
        decision = "insufficient_evidence"
    elif conflict_detected:
        decision = "evidence_conflict"
    else:
        decision = "ready_for_grounded_answer"

    return {
        "question": clean_question,
        "contract": contract.name,
        "decision": decision,
        "stop_reason": stop_reason,
        "found_slots": {
            name: _public_evidence(result) for name, result in found.items()
        },
        "missing_slots": missing,
        "conflict": {
            "detected": conflict_detected,
            "values": list(contract.conflict_values),
            "action": (
                "保留冲突并按来源状态或人工规则裁决，不能让生成模型自行选一个。"
                if conflict_detected
                else None
            ),
        },
        "trace": trace,
        "budgets": {
            "max_rounds": settings.investigation_max_rounds,
            "max_queries": settings.investigation_max_queries,
            "queries_used": len(trace),
        },
        "took_ms": round((time.perf_counter() - started) * 1000),
    }
