from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from config import load_settings
from rag_store import MilvusVectorStore, VectorStoreError


class AliasClient:
    def __init__(self, aliases: dict[str, str] | None = None) -> None:
        self.aliases = dict(aliases or {})
        self.describe_calls = 0

    def has_collection(self, **_: Any) -> bool:
        return False

    def list_aliases(self, **_: Any) -> dict[str, Any]:
        return {"aliases": list(self.aliases), "db_name": "default"}

    def describe_alias(self, *, alias: str, **_: Any) -> dict[str, str]:
        self.describe_calls += 1
        return {"alias": alias, "collection": self.aliases[alias]}


def make_vectors(client: Any, collection: str = "physical_v1") -> MilvusVectorStore:
    settings = replace(load_settings(), milvus_collection=collection)
    return MilvusVectorStore(settings, "test-repo", client=client)


def test_missing_alias_is_filtered_by_list_aliases_without_describe_error() -> None:
    client = AliasClient()
    vectors = make_vectors(client)

    assert vectors.alias_target("active_alias") is None
    assert vectors._has_collection() is False
    assert client.describe_calls == 0


def test_existing_alias_is_described_after_list_normalization() -> None:
    client = AliasClient({"active_alias": "physical_v1"})
    vectors = make_vectors(client)

    assert vectors.alias_target("active_alias") == "physical_v1"
    assert client.describe_calls == 1
    assert MilvusVectorStore._listed_alias_names(
        {"aliases": [{"alias": "one"}, {"name": "two"}]}
    ) == {"one", "two"}


def test_alias_listing_dependency_error_is_not_reported_as_missing_alias() -> None:
    class BrokenClient(AliasClient):
        def list_aliases(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("connection lost")

    with pytest.raises(VectorStoreError, match="connection lost"):
        make_vectors(BrokenClient()).alias_target("active_alias")
