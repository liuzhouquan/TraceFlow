from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from config import load_settings
from quality_gate import evaluate_release
from rag_store import SearchResult


class AcceptedPipeline:
    async def retrieve(self, *_: Any) -> Any:
        return SimpleNamespace(
            results=[
                SearchResult(
                    id="chunk-1",
                    document_id="document-1",
                    title="Policy",
                    filename="policy.md",
                    chunk_index=0,
                    content="This evidence is relevant enough by vector score.",
                    score=0.9,
                    metadata={},
                    trace={"dense_score": 0.9},
                )
            ]
        )

    def _answer_gate_decision(self, *_: Any) -> tuple[bool, dict[str, Any]]:
        return True, {"reason": "dense_score"}


def test_no_answer_case_uses_the_same_union_marker_gate_as_ask(
    tmp_path: Path,
) -> None:
    cases = tmp_path / "negative.jsonl"
    cases.write_text(
        '{"id":"future","kind":"no_answer","query":"What changed in 2028?"}\n',
        encoding="utf-8",
    )
    settings = replace(
        load_settings(),
        quality_recall_at_k_min=0.0,
        quality_mrr_min=0.0,
        quality_ndcg_at_k_min=0.0,
    )

    report = asyncio.run(
        evaluate_release(
            settings=settings,
            pipeline=AcceptedPipeline(),  # type: ignore[arg-type]
            cases_path=cases,
        )
    )

    assert report["passed"] is True
    assert report["metrics"]["negative_rejection_rate"] == 1.0
    assert report["cases"][0]["detail"] == "union_marker_gate_rejected_evidence"
    assert len(report["dataset_sha256"]) == 64
