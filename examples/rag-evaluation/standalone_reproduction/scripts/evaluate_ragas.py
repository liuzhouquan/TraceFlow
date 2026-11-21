from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx


FINAL_STATES = {"passed", "failed", "partial", "error"}
LABELS = {
    "context_precision": "Context Precision",
    "context_recall": "Context Recall",
    "faithfulness": "Faithfulness",
    "answer_relevancy": "Answer Relevancy",
}


def request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> Any:
    response = client.request(method, path, **kwargs)
    if response.is_error:
        raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text}")
    return response.json()


def wait_for_run(client: httpx.Client, path: str, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = request(client, "GET", path)
        if payload.get("status") in FINAL_STATES:
            return payload
        time.sleep(0.5)
    raise RuntimeError(f"RAGAS evaluation did not finish within {timeout} seconds")


def render(payload: dict[str, Any]) -> None:
    result = payload.get("result") or {}
    hard_gate = result.get("hard_gate") or {}
    judge = result.get("judge") or {}
    print(f"run_id: {payload.get('run_id')}")
    print(f"status: {payload.get('status')}")
    print(
        "hard gate: "
        f"{hard_gate.get('passed_cases', 0)}/{hard_gate.get('total_cases', 0)} cases passed"
    )
    if judge.get("requested"):
        print(
            "judge: "
            f"{judge.get('model') or '-'} / Ragas {judge.get('ragas_version') or 'unavailable'}"
        )
    if judge.get("error"):
        print(f"judge error: {judge['error']}")
    metrics = (result.get("ragas_gate") or {}).get("metrics") or {}
    for name, label in LABELS.items():
        item = metrics.get(name) or {}
        score = item.get("score")
        threshold = item.get("threshold")
        rendered_score = "--" if score is None else f"{float(score):.3f}"
        rendered_threshold = "--" if threshold is None else f"{float(threshold):.3f}"
        state = "SKIP" if item.get("passed") is None else ("PASS" if item["passed"] else "FAIL")
        print(f"{label:20} {rendered_score}  threshold={rendered_threshold}  {state}")
    print("\ncase details:")
    for case in result.get("cases") or []:
        hard = "PASS" if (case.get("hard_rules") or {}).get("passed") else "FAIL"
        semantic = case.get("ragas") or {}
        faithfulness = semantic.get("faithfulness")
        faithfulness_text = "--" if faithfulness is None else f"{faithfulness:.3f}"
        print(
            f"- {case.get('id')}: hard={hard}, faithfulness={faithfulness_text}, "
            f"mode={case.get('generation_mode')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run TraceFlow RAG Studio generation quality evaluation"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8077")
    parser.add_argument("--repo-id", default="traceflow")
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument(
        "--mode",
        choices=("dense", "hybrid", "hybrid_rerank"),
        default="hybrid",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="run Ragas metrics with the configured Judge; this consumes model calls",
    )
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    if not 1 <= args.top_k <= 20:
        parser.error("--top-k must be between 1 and 20")
    with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
        prefix = f"/api/repositories/{args.repo_id}/evaluations/ragas"
        created = request(
            client,
            "POST",
            prefix,
            json={
                "run_judge": args.judge,
                "top_k": args.top_k,
                "mode": args.mode,
            },
        )
        payload = wait_for_run(
            client,
            f"{prefix}/{created['run_id']}",
            args.timeout,
        )

    render(payload)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nreport saved to: {args.json_output}")

    result = payload.get("result") or {}
    if payload.get("status") == "error":
        print(payload.get("error") or "evaluation error", file=sys.stderr)
        return 2
    if not (result.get("hard_gate") or {}).get("passed"):
        return 1
    if args.judge and not result.get("passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
