from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Any

import httpx

from config import Settings
from rag_store import SearchResult


@dataclass(frozen=True)
class RerankOutcome:
    results: list[SearchResult]
    trace: dict[str, Any]


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set(re.findall(r"[a-z0-9_./:%-]+", lowered))
    for segment in re.findall(r"[\u4e00-\u9fff]+", lowered):
        tokens.update(segment)
        tokens.update(
            segment[index : index + 2]
            for index in range(max(0, len(segment) - 1))
        )
    return tokens


def _normalise(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return max(0.0, min(1.0, value))


class Reranker:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._last_error: str | None = None

    @property
    def configured(self) -> bool:
        if self.settings.rerank_provider == "disabled":
            return False
        if self.settings.rerank_provider == "heuristic":
            return True
        return bool(
            self.settings.rerank_base_url
            and self.settings.rerank_api_key
            and self.settings.rerank_model
        )

    def status(self) -> dict[str, Any]:
        provider = self.settings.rerank_provider
        if provider == "qwen_api":
            detail = (
                "真实 Qwen Rerank API 已配置"
                if self.configured
                else "未配置 RERANK_API_KEY，hybrid_rerank 会明确降级为 hybrid"
            )
        elif provider == "heuristic":
            detail = "启发式重排仅作为可解释基线，不等同于模型 Rerank"
        else:
            detail = "Rerank 已关闭"
        if self._last_error:
            detail = f"{detail}；最近一次调用失败：{self._last_error}"
        return {
            "provider": provider,
            "model": self.settings.rerank_model if provider == "qwen_api" else None,
            "available": self.configured,
            "fallback": "保留 Milvus Hybrid + RRF 排名",
            "detail": detail,
        }

    @staticmethod
    def _document_text(result: SearchResult) -> str:
        section_path = result.metadata.get("section_path") or []
        section = " > ".join(str(item) for item in section_path)
        return "\n".join(
            value
            for value in (result.title, result.filename, section, result.content)
            if value
        )

    async def _qwen_rerank(
        self,
        query: str,
        candidates: list[SearchResult],
        top_k: int,
    ) -> list[SearchResult]:
        endpoint = self.settings.rerank_base_url.rstrip("/")
        if not endpoint.endswith("/reranks"):
            endpoint = f"{endpoint}/reranks"
        documents = [self._document_text(candidate) for candidate in candidates]
        request_body: dict[str, Any] = {
            "model": self.settings.rerank_model,
            "query": query,
            "documents": documents,
            "top_n": min(top_k, len(documents)),
        }
        if self.settings.rerank_instruction:
            request_body["instruct"] = self.settings.rerank_instruction
        async with httpx.AsyncClient(
            timeout=self.settings.rerank_timeout_seconds,
            transport=self._transport,
        ) as client:
            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {self.settings.rerank_api_key}"},
                json=request_body,
            )
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, dict):
            raise ValueError("Rerank API 返回值不是 JSON 对象")
        output = payload.get("output")
        nested_rows = output.get("results") if isinstance(output, dict) else None
        rows = payload.get("results") or nested_rows or []
        if not isinstance(rows, list):
            raise ValueError("Rerank API 没有返回 results 列表")
        expected_count = min(top_k, len(candidates))
        if len(rows) != expected_count:
            raise ValueError(
                "Rerank API 返回候选数量不完整："
                f"期望 {expected_count} 条，实际 {len(rows)} 条"
            )
        ranked: list[SearchResult] = []
        used: set[int] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Rerank API results 包含非对象元素")
            raw_index = row.get("index")
            if isinstance(raw_index, bool):
                raise ValueError("Rerank API 返回了无效候选下标")
            try:
                index = int(raw_index)
            except (TypeError, ValueError) as exc:
                raise ValueError("Rerank API 返回了无效候选下标") from exc
            if index < 0 or index >= len(candidates) or index in used:
                raise ValueError("Rerank API 返回了越界或重复的候选下标")
            raw_score = row.get("relevance_score", row.get("score"))
            try:
                score = _normalise(float(raw_score))
            except (TypeError, ValueError) as exc:
                raise ValueError("Rerank API 返回了无效相关性分数") from exc
            candidate = candidates[index]
            ranked.append(
                replace(
                    candidate,
                    score=score,
                    trace={
                        **candidate.trace,
                        "rerank_requested": True,
                        "rerank_provider": "qwen_api",
                        "rerank_mode": "qwen_api",
                        "rerank_model": self.settings.rerank_model,
                        "rerank_score": round(score, 6),
                    },
                )
            )
            used.add(index)
        return ranked

    @staticmethod
    def _heuristic_rerank(
        query: str,
        candidates: list[SearchResult],
        top_k: int,
    ) -> list[SearchResult]:
        query_terms = _tokens(query)
        scored: list[tuple[float, int, SearchResult]] = []
        for index, candidate in enumerate(candidates):
            text_terms = _tokens(Reranker._document_text(candidate))
            coverage = (
                len(query_terms & text_terms) / max(1, len(query_terms))
                if query_terms
                else 0.0
            )
            both_routes = len(candidate.trace.get("sources") or []) > 1
            score = _normalise(0.7 * coverage + (0.15 if both_routes else 0.0) + 0.15 / (index + 1))
            scored.append((score, index, candidate))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            replace(
                candidate,
                score=score,
                trace={
                    **candidate.trace,
                    "rerank_requested": True,
                    "rerank_provider": "heuristic",
                    "rerank_mode": "heuristic_baseline",
                    "rerank_model": None,
                    "rerank_score": round(score, 6),
                },
            )
            for score, _, candidate in scored[:top_k]
        ]

    async def rerank(
        self,
        query: str,
        candidates: list[SearchResult],
        top_k: int,
    ) -> RerankOutcome:
        requested_top_k = min(max(0, top_k), len(candidates))
        provider = self.settings.rerank_provider
        base_trace = {
            "requested": True,
            "provider": provider,
            "model": self.settings.rerank_model if provider == "qwen_api" else None,
            "candidate_count": len(candidates),
            "requested_top_k": requested_top_k,
        }
        if not candidates:
            return RerankOutcome(
                [],
                {
                    **base_trace,
                    "mode": "not_run",
                    "applied": False,
                    "fallback": False,
                    "result_count": 0,
                    "reason": "no_candidates",
                },
            )
        if provider == "disabled":
            return RerankOutcome(
                [
                    replace(
                        candidate,
                        trace={
                            **candidate.trace,
                            "rerank_requested": True,
                            "rerank_provider": "disabled",
                            "rerank_mode": "fallback_hybrid",
                            "rerank_model": None,
                            "rerank_score": None,
                        },
                    )
                    for candidate in candidates[:top_k]
                ],
                {
                    **base_trace,
                    "mode": "disabled",
                    "applied": False,
                    "fallback": True,
                    "fallback_to": "hybrid_rrf",
                    "result_count": requested_top_k,
                    "reason": "RERANK_PROVIDER=disabled",
                },
            )
        if provider == "heuristic":
            ranked = self._heuristic_rerank(query, candidates, top_k)
            return RerankOutcome(
                ranked,
                {
                    **base_trace,
                    "mode": "heuristic_baseline",
                    "applied": True,
                    "fallback": False,
                    "result_count": len(ranked),
                    "baseline": True,
                    "warning": "启发式基线不等同于真实模型 Rerank",
                },
            )
        if not self.configured:
            return RerankOutcome(
                [
                    replace(
                        candidate,
                        trace={
                            **candidate.trace,
                            "rerank_requested": True,
                            "rerank_provider": "qwen_api",
                            "rerank_mode": "fallback_hybrid",
                            "rerank_model": self.settings.rerank_model,
                            "rerank_score": None,
                        },
                    )
                    for candidate in candidates[:top_k]
                ],
                {
                    **base_trace,
                    "mode": "fallback_hybrid",
                    "applied": False,
                    "fallback": True,
                    "fallback_to": "hybrid_rrf",
                    "result_count": requested_top_k,
                    "reason": "RERANK_API_KEY 未配置",
                },
            )
        try:
            ranked = await self._qwen_rerank(query, candidates, top_k)
            self._last_error = None
            return RerankOutcome(
                ranked,
                {
                    **base_trace,
                    "mode": "qwen_api",
                    "applied": True,
                    "fallback": False,
                    "result_count": len(ranked),
                },
            )
        except Exception as exc:
            self._last_error = str(exc)
            return RerankOutcome(
                [
                    replace(
                        candidate,
                        trace={
                            **candidate.trace,
                            "rerank_requested": True,
                            "rerank_provider": "qwen_api",
                            "rerank_mode": "fallback_hybrid",
                            "rerank_model": self.settings.rerank_model,
                            "rerank_score": None,
                            "rerank_error": str(exc),
                        },
                    )
                    for candidate in candidates[:top_k]
                ],
                {
                    **base_trace,
                    "mode": "fallback_hybrid",
                    "applied": False,
                    "fallback": True,
                    "fallback_to": "hybrid_rrf",
                    "result_count": requested_top_k,
                    "reason": str(exc),
                },
            )
