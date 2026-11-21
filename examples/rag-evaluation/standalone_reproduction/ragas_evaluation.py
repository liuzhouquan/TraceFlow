from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from config import Settings
from model_client import ModelClient
from rag_pipeline import RAGPipeline


METRIC_NAMES = (
    "context_precision",
    "context_recall",
    "faithfulness",
    "answer_relevancy",
)
CASE_KINDS = {"positive", "no_answer", "conflict"}


class RagasUnavailableError(RuntimeError):
    """Ragas、Judge 或评测 Embedding 不可用。"""


@dataclass(frozen=True)
class GenerationEvalCase:
    id: str
    kind: str
    query: str
    reference: str
    relevant_documents: tuple[str, ...]
    must_include: tuple[str, ...]
    must_not_include: tuple[str, ...]
    conflict_values: tuple[str, ...] = ()


class SemanticScorer(Protocol):
    version: str

    async def score(
        self,
        *,
        user_input: str,
        response: str,
        retrieved_contexts: list[str],
        reference: str,
    ) -> dict[str, float]: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_strings(value: Any, field: str, case_id: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"Case {case_id} 的 {field} 必须是数组")
    return tuple(str(item).strip() for item in value if str(item).strip())


def load_generation_cases(path: Path) -> list[GenerationEvalCase]:
    if not path.is_file():
        raise ValueError(f"生成评测集不存在：{path}")
    cases: list[GenerationEvalCase] = []
    identifiers: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"生成评测集第 {line_number} 行不是合法 JSON") from exc
        case_id = str(row.get("id") or "").strip()
        kind = str(row.get("kind") or "positive").strip()
        query = str(row.get("query") or "").strip()
        reference = str(row.get("reference") or "").strip()
        if not case_id or not query or not reference:
            raise ValueError(f"生成评测集第 {line_number} 行缺少 id、query 或 reference")
        if case_id in identifiers:
            raise ValueError(f"生成评测集包含重复 id：{case_id}")
        if kind not in CASE_KINDS:
            raise ValueError(f"Case {case_id} 的 kind 不支持：{kind}")
        identifiers.add(case_id)
        cases.append(
            GenerationEvalCase(
                id=case_id,
                kind=kind,
                query=query,
                reference=reference,
                relevant_documents=_clean_strings(
                    row.get("relevant_documents"), "relevant_documents", case_id
                ),
                must_include=_clean_strings(row.get("must_include"), "must_include", case_id),
                must_not_include=_clean_strings(
                    row.get("must_not_include"), "must_not_include", case_id
                ),
                conflict_values=_clean_strings(
                    row.get("conflict_values"), "conflict_values", case_id
                ),
            )
        )
    if not cases:
        raise ValueError("生成评测集不能为空")
    return cases


def _dataset_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mean(values: list[float]) -> float | None:
    finite = [value for value in values if math.isfinite(value)]
    if not finite:
        return None
    return round(sum(finite) / len(finite), 4)


def _contains(text: str, expected: str) -> bool:
    normalized_text = re.sub(r"\s+", "", text).lower()
    normalized_expected = re.sub(r"\s+", "", expected).lower()
    return normalized_expected in normalized_text


class RagasCollectionsScorer:
    """Ragas 0.4 Collections API 与项目模型客户端之间的适配层。"""

    def __init__(self, settings: Settings, models: ModelClient) -> None:
        self.settings = settings
        self.models = models
        if settings.provider == "deterministic":
            raise RagasUnavailableError(
                "deterministic Embedding 只用于离线冒烟，不能作为 Answer Relevancy 的语义向量；"
                "请改用 ollama 或 openai_compatible"
            )
        try:
            from openai import AsyncOpenAI
            from ragas.embeddings.base import BaseRagasEmbedding
            from ragas.llms import llm_factory
            from ragas.metrics.collections import (
                AnswerRelevancy,
                ContextPrecision,
                ContextRecall,
                Faithfulness,
            )
        except Exception as exc:
            raise RagasUnavailableError(
                "无法导入 Ragas 0.4 Collections API。请按 requirements.txt 安装固定依赖："
                f"{exc}"
            ) from exc

        class ProjectEmbedding(BaseRagasEmbedding):
            def __init__(self, client: ModelClient) -> None:
                super().__init__()
                self.client = client

            def embed_text(self, text: str, **_: Any) -> list[float]:
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    return asyncio.run(self.aembed_text(text))
                raise RuntimeError("异步评测中不能调用同步 Embedding")

            async def aembed_text(self, text: str, **_: Any) -> list[float]:
                rows = await self.client.embed_documents([text])
                return rows[0] if rows else []

            def embed_texts(self, texts: list[str], **_: Any) -> list[list[float]]:
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    return asyncio.run(self.aembed_texts(texts))
                raise RuntimeError("异步评测中不能调用同步 Embedding")

            async def aembed_texts(
                self, texts: list[str], **_: Any
            ) -> list[list[float]]:
                return await self.client.embed_documents(texts)

        api_key = settings.ragas_judge_api_key
        if not api_key and re.search(r"(?:127\.0\.0\.1|localhost)", settings.ragas_judge_base_url):
            api_key = "local-judge"
        if not api_key:
            raise RagasUnavailableError("RAGAS_JUDGE_API_KEY 未配置")
        if not settings.ragas_judge_base_url or not settings.ragas_judge_model:
            raise RagasUnavailableError("RAGAS Judge Base URL 或模型未配置")

        try:
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=settings.ragas_judge_base_url,
                timeout=settings.ragas_timeout_seconds,
            )
            llm = llm_factory(
                settings.ragas_judge_model,
                client=client,
                temperature=0,
                max_tokens=settings.ragas_judge_max_tokens,
            )
            embeddings = ProjectEmbedding(models)
            self.metrics = {
                "context_precision": ContextPrecision(llm=llm),
                "context_recall": ContextRecall(llm=llm),
                "faithfulness": Faithfulness(llm=llm),
                "answer_relevancy": AnswerRelevancy(
                    llm=llm,
                    embeddings=embeddings,
                    strictness=settings.ragas_answer_relevancy_strictness,
                ),
            }
            self.version = importlib.metadata.version("ragas")
        except Exception as exc:
            raise RagasUnavailableError(f"Ragas Judge 初始化失败：{exc}") from exc

    async def score(
        self,
        *,
        user_input: str,
        response: str,
        retrieved_contexts: list[str],
        reference: str,
    ) -> dict[str, float]:
        calls = {
            "context_precision": self.metrics["context_precision"].ascore(
                user_input=user_input,
                reference=reference,
                retrieved_contexts=retrieved_contexts,
            ),
            "context_recall": self.metrics["context_recall"].ascore(
                user_input=user_input,
                reference=reference,
                retrieved_contexts=retrieved_contexts,
            ),
            "faithfulness": self.metrics["faithfulness"].ascore(
                user_input=user_input,
                response=response,
                retrieved_contexts=retrieved_contexts,
            ),
            "answer_relevancy": self.metrics["answer_relevancy"].ascore(
                user_input=user_input,
                response=response,
            ),
        }
        names = list(calls)
        results = await asyncio.gather(
            *(
                asyncio.wait_for(calls[name], timeout=self.settings.ragas_timeout_seconds)
                for name in names
            )
        )
        return {
            name: round(float(result.value), 4)
            for name, result in zip(names, results, strict=True)
        }


def _hard_rule_result(case: GenerationEvalCase, answer_payload: dict[str, Any]) -> dict[str, Any]:
    answer = str(answer_payload.get("answer") or "")
    sources = list(answer_payload.get("sources") or [])
    filenames = [str(source.get("filename") or "") for source in sources]
    expected = set(case.relevant_documents)
    retrieved = set(filenames)
    document_recall = (
        len(expected & retrieved) / len(expected)
        if expected
        else (1.0 if not sources else 0.0)
    )
    missing_phrases = [value for value in case.must_include if not _contains(answer, value)]
    forbidden_phrases = [value for value in case.must_not_include if _contains(answer, value)]
    missing_conflicts = [value for value in case.conflict_values if not _contains(answer, value)]
    mode = str(answer_payload.get("generation_mode") or "")
    if case.kind == "no_answer":
        mode_passed = mode == "no_evidence"
    else:
        mode_passed = mode != "no_evidence"
    passed = (
        document_recall >= 1.0
        and not missing_phrases
        and not forbidden_phrases
        and not missing_conflicts
        and mode_passed
    )
    return {
        "passed": passed,
        "document_recall": round(document_recall, 4),
        "mode_passed": mode_passed,
        "missing_phrases": missing_phrases,
        "forbidden_phrases": forbidden_phrases,
        "missing_conflict_values": missing_conflicts,
    }


async def evaluate_generation_quality(
    *,
    settings: Settings,
    pipeline: RAGPipeline,
    run_judge: bool,
    top_k: int | None = None,
    mode: str | None = None,
    cases: list[GenerationEvalCase] | None = None,
    scorer: SemanticScorer | None = None,
) -> dict[str, Any]:
    evaluation_cases = cases or load_generation_cases(settings.ragas_evaluation_set)
    effective_top_k = top_k or settings.release_top_k
    effective_mode = mode or settings.retrieval_mode
    pipeline_status = await pipeline.status()
    started_at = _now()
    judge_error: str | None = None
    if run_judge and scorer is None:
        try:
            scorer = RagasCollectionsScorer(settings, pipeline.models)
        except RagasUnavailableError as exc:
            judge_error = str(exc)

    case_rows: list[dict[str, Any]] = []
    aggregate_values: dict[str, list[float]] = {name: [] for name in METRIC_NAMES}
    for case in evaluation_cases:
        answer_payload = await pipeline.ask(case.query, effective_top_k, effective_mode)
        sources = list(answer_payload.get("sources") or [])
        contexts = [str(source.get("snippet") or "") for source in sources if source.get("snippet")]
        hard_rules = _hard_rule_result(case, answer_payload)
        semantic_scores: dict[str, float] | None = None
        semantic_error: str | None = None
        semantic_skipped_reason: str | None = None
        if not run_judge:
            semantic_skipped_reason = "judge_disabled"
        elif scorer is None:
            semantic_skipped_reason = "judge_unavailable"
        elif case.kind == "no_answer":
            semantic_skipped_reason = "no_answer_uses_hard_rules"
        elif not contexts:
            semantic_skipped_reason = "no_retrieved_contexts"
        else:
            try:
                semantic_scores = await scorer.score(
                    user_input=case.query,
                    response=str(answer_payload.get("answer") or ""),
                    retrieved_contexts=contexts,
                    reference=case.reference,
                )
                for name, value in semantic_scores.items():
                    if name in aggregate_values and math.isfinite(value):
                        aggregate_values[name].append(value)
            except Exception as exc:
                semantic_error = f"{type(exc).__name__}: {exc}"

        case_rows.append(
            {
                "id": case.id,
                "kind": case.kind,
                "user_input": case.query,
                "reference": case.reference,
                "response": answer_payload.get("answer"),
                "generation_mode": answer_payload.get("generation_mode"),
                "retrieved_contexts": contexts,
                "retrieved_documents": [str(source.get("filename") or "") for source in sources],
                "source_ids": [str(source.get("id") or "") for source in sources],
                "took_ms": answer_payload.get("took_ms"),
                "hard_rules": hard_rules,
                "ragas": semantic_scores,
                "ragas_error": semantic_error,
                "ragas_skipped_reason": semantic_skipped_reason,
            }
        )

    thresholds = {
        "context_precision": settings.ragas_context_precision_min,
        "context_recall": settings.ragas_context_recall_min,
        "faithfulness": settings.ragas_faithfulness_min,
        "answer_relevancy": settings.ragas_answer_relevancy_min,
    }
    metrics: dict[str, dict[str, Any]] = {}
    for name in METRIC_NAMES:
        score = _mean(aggregate_values[name])
        threshold = thresholds[name]
        metrics[name] = {
            "score": score,
            "threshold": threshold,
            "passed": None if score is None else score >= threshold,
            "scored_cases": len(aggregate_values[name]),
        }

    hard_passed = all(row["hard_rules"]["passed"] for row in case_rows)
    ragas_case_errors = [row for row in case_rows if row["ragas_error"]]
    ragas_available = run_judge and scorer is not None and any(
        row["ragas"] is not None for row in case_rows
    )
    ragas_passed = ragas_available and not ragas_case_errors and all(
        value["passed"] is True for value in metrics.values()
    )
    if run_judge and (scorer is None or ragas_case_errors):
        status = "error"
    elif not hard_passed:
        status = "failed"
    elif run_judge and ragas_available and not ragas_passed:
        status = "failed"
    elif run_judge and ragas_passed:
        status = "passed"
    else:
        status = "partial"

    return {
        "status": status,
        "passed": status == "passed",
        "started_at": started_at,
        "completed_at": _now(),
        "evaluation_set": {
            "name": settings.ragas_evaluation_set.name,
            "sha256": _dataset_hash(settings.ragas_evaluation_set),
            "case_count": len(evaluation_cases),
        },
        "pipeline": {
            "retrieval_mode": effective_mode,
            "top_k": effective_top_k,
            "alias": pipeline_status.get("vector_store", {}).get("alias"),
            "physical_collection": pipeline_status.get("vector_store", {}).get("alias_target"),
            "generation_model": pipeline_status.get("generation", {}).get("model"),
            "embedding_provider": pipeline.models.embedding_provider,
            "embedding_model": pipeline.models.embedding_model,
        },
        "judge": {
            "requested": run_judge,
            "available": ragas_available,
            "provider": "openai_compatible",
            "model": settings.ragas_judge_model if run_judge else None,
            "ragas_version": scorer.version if scorer is not None else None,
            "prompt_version": settings.ragas_prompt_version if run_judge else None,
            "input_language": settings.ragas_input_language if run_judge else None,
            "error": judge_error or (
                f"{len(ragas_case_errors)} 个 Case 的 RAGAS 指标计算失败"
                if ragas_case_errors
                else None
            ),
        },
        "hard_gate": {
            "passed": hard_passed,
            "passed_cases": sum(row["hard_rules"]["passed"] for row in case_rows),
            "total_cases": len(case_rows),
        },
        "ragas_gate": {
            "available": ragas_available,
            "passed": ragas_passed if ragas_available else None,
            "metrics": metrics,
            "case_errors": len(ragas_case_errors),
        },
        "cases": case_rows,
    }


class EvaluationRunStore:
    """用原子 JSON 文件保存异步评测状态和完整报告。"""

    def __init__(self, settings: Settings) -> None:
        self.directory = settings.catalog_path.parent / "evaluations" / "ragas"
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f-]{36}", run_id):
            raise ValueError("evaluation run id 无效")
        return self.directory / f"{run_id}.json"

    def _write(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._path(str(payload["run_id"]))
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
        return payload

    def create(self, *, run_judge: bool, top_k: int, mode: str) -> dict[str, Any]:
        now = _now()
        return self._write(
            {
                "run_id": str(uuid.uuid4()),
                "status": "queued",
                "created_at": now,
                "updated_at": now,
                "config": {"run_judge": run_judge, "top_k": top_k, "mode": mode},
                "result": None,
                "error": None,
            }
        )

    def update(
        self,
        run_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        payload = self.get(run_id)
        payload.update(
            {
                "status": status,
                "updated_at": _now(),
                "result": result,
                "error": error,
            }
        )
        return self._write(payload)

    def get(self, run_id: str) -> dict[str, Any]:
        path = self._path(run_id)
        if not path.is_file():
            raise ValueError("没有找到这个 RAGAS evaluation run")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = []
        for path in sorted(
            self.directory.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )[: max(1, min(limit, 100))]:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        return rows
