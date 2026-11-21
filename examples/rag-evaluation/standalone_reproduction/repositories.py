from __future__ import annotations

import re
from dataclasses import dataclass, replace

from config import Settings


@dataclass(frozen=True)
class Repository:
    id: str
    full_name: str
    description: str

    def public_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "full_name": self.full_name,
            "description": self.description,
        }


REPOSITORIES = (
    Repository(
        id="devflow-ai",
        full_name="course/devflow-ai",
        description="本课程要逐步实现的 DevFlow AI 项目知识库",
    ),
    Repository(
        id="isolation-lab",
        full_name="course/isolation-lab",
        description="用于验证不同项目不会互相召回资料的隔离项目",
    ),
)


def get_repository(repo_id: str) -> Repository | None:
    return next((repo for repo in REPOSITORIES if repo.id == repo_id), None)


def settings_for_repository(settings: Settings, repo_id: str) -> Settings:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", repo_id):
        raise ValueError("repo_id 格式无效")
    suffix = repo_id.replace("-", "_")
    # Keep repository-scoped state under the configured catalog root so
    # demos and tests can use an isolated runtime directory.
    root = settings.catalog_path.parent / "repositories" / repo_id
    upload_dir = root / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return replace(
        settings,
        catalog_path=root / "catalog.sqlite3",
        upload_dir=upload_dir,
        knowledge_base_name=f"DevFlow RAG 工程化 · {repo_id}",
        milvus_collection=f"{settings.milvus_collection}_{suffix}",
        milvus_collection_prefix=f"{settings.milvus_collection_prefix}_{suffix}",
    )
