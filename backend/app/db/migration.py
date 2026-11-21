import logging
import os
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import settings

logger = logging.getLogger(__name__)


def _inspect_and_adopt_legacy_database(inspector, alembic_cfg: Config) -> None:
    table_names = set(inspector.get_table_names())
    if "users" not in table_names or "alembic_version" in table_names:
        return

    user_cols = {c["name"] for c in inspector.get_columns("users")}
    repo_cols = (
        {c["name"] for c in inspector.get_columns("repositories")}
        if "repositories" in table_names
        else set()
    )

    req_0002_user_cols = {"email", "hashed_password", "is_active"}
    req_0002_repo_cols = {"created_by", "is_public"}
    has_0002_table = "repository_members" in table_names

    is_full_0002 = (
        has_0002_table
        and req_0002_user_cols.issubset(user_cols)
        and req_0002_repo_cols.issubset(repo_cols)
    )

    is_baseline_0001 = (
        not has_0002_table
        and req_0002_user_cols.isdisjoint(user_cols)
        and req_0002_repo_cols.isdisjoint(repo_cols)
    )

    if is_full_0002:
        logger.info("检测到结构完整且已包含认证隔离的存量数据库，自动标记版本为 head...")
        command.stamp(alembic_cfg, "head")
    elif is_baseline_0001:
        logger.info("检测到未迁移的完整存量基线数据库，自动标记版本为 0001_initial...")
        command.stamp(alembic_cfg, "0001_initial")
    else:
        missing_parts = []
        if not has_0002_table:
            missing_parts.append("表 repository_members 缺失")
        if not req_0002_user_cols.issubset(user_cols):
            missing_parts.append(f"users 列缺失: {req_0002_user_cols - user_cols}")
        if not req_0002_repo_cols.issubset(repo_cols):
            missing_parts.append(f"repositories 列缺失: {req_0002_repo_cols - repo_cols}")
        raise RuntimeError(
            f"检测到不完整的存量数据库结构（{', '.join(missing_parts)}），无法安全自动纳管，拒绝启动以防止数据状态损坏。"
        )


def run_migrations(db_url: str | None = None, bind: Any = None) -> None:
    """运行 Alembic 数据库迁移升级到最新版本 (head)，并自动纳管存量数据库。"""
    backend_dir = Path(__file__).resolve().parent.parent.parent
    alembic_ini = backend_dir / "alembic.ini"
    if not alembic_ini.exists():
        logger.warning("未找到 alembic.ini，跳过数据库自动迁移。")
        return

    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))

    target_url = None
    if bind is not None:
        alembic_cfg.attributes["connection"] = bind
        if hasattr(bind, "url"):
            target_url = str(bind.url)
        elif hasattr(bind, "engine") and hasattr(bind.engine, "url"):
            target_url = str(bind.engine.url)
    else:
        target_url = db_url or os.getenv("DATABASE_URL") or settings.database_url

    if target_url:
        alembic_cfg.set_main_option("sqlalchemy.url", target_url)

    # 存量数据库纳管判定：检查是否已有业务表但尚未创建 alembic_version
    if bind is not None:
        inspector = inspect(bind)
        _inspect_and_adopt_legacy_database(inspector, alembic_cfg)
    else:
        check_engine = create_engine(target_url)
        try:
            with check_engine.connect() as conn:
                inspector = inspect(conn)
                _inspect_and_adopt_legacy_database(inspector, alembic_cfg)
        finally:
            check_engine.dispose()

    logger.info("正在执行数据库版本迁移 (alembic upgrade head)...")
    command.upgrade(alembic_cfg, "head")
    logger.info("数据库版本迁移已完成。")
