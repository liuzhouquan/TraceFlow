from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as app_module
from rag_pipeline import RetrievalOutcome


class RunningBuild:
    def locked(self) -> bool:
        return True


def test_queries_keep_serving_while_a_candidate_generation_is_building(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_retrieve(*_: object, **__: object) -> RetrievalOutcome:
        return RetrievalOutcome(
            results=[],
            took_ms=3,
            trace={"serving": {"physical_collection": "previous_generation"}},
        )

    _store, pipeline, releases = app_module._runtime("devflow-ai")
    monkeypatch.setattr(releases, "_build_lock", RunningBuild())
    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    client = TestClient(app_module.app)

    response = client.post(
        "/api/repositories/devflow-ai/search",
        json={"query": "回滚条件", "mode": "hybrid"},
    )

    assert response.status_code == 200
    assert response.json()["retrieval"]["serving"]["physical_collection"] == "previous_generation"


def test_ask_keeps_serving_while_a_candidate_generation_is_building(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ask(*_: object, **__: object) -> dict[str, object]:
        return {
            "answer": "旧版本仍在服务",
            "sources": [],
            "retrieval": {"serving": {"physical_collection": "previous_generation"}},
        }

    _store, pipeline, releases = app_module._runtime("devflow-ai")
    monkeypatch.setattr(releases, "_build_lock", RunningBuild())
    monkeypatch.setattr(pipeline, "ask", fake_ask)
    client = TestClient(app_module.app)

    response = client.post(
        "/api/repositories/devflow-ai/ask",
        json={"question": "回滚条件", "mode": "hybrid"},
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "旧版本仍在服务"
