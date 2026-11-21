import logging
import os
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import settings
from app.db.models import Base

logger = logging.getLogger(__name__)


def _missing_foreign_keys(inspector, table_names: set[str], excluded_columns=None) -> list[str]:
    """Compare FK endpoints, independent of generated constraint names."""
    excluded_columns = excluded_columns or {}
    missing = []
    for table_name in sorted(table_names):
        actual = {
            (
                tuple(fk["constrained_columns"]),
                fk["referred_table"],
                tuple(fk["referred_columns"]),
            )
            for fk in inspector.get_foreign_keys(table_name)
        }
        for constraint in Base.metadata.tables[table_name].foreign_key_constraints:
            columns = tuple(element.parent.name for element in constraint.elements)
            if set(columns) & excluded_columns.get(table_name, set()):
                continue
            target_table = constraint.referred_table.name
            target_columns = tuple(element.column.name for element in constraint.elements)
            if (columns, target_table, target_columns) not in actual:
                missing.append(
                    f"{table_name}({', '.join(columns)}) -> "
                    f"{target_table}({', '.join(target_columns)})"
                )
    return sorted(missing)


def _inspect_and_adopt_legacy_database(inspector, alembic_cfg: Config) -> None:
    raw_tables = set(inspector.get_table_names())
    user_tables = raw_tables - {"sqlite_sequence", "alembic_version"}

    # 1. 全新空库：交给 alembic 从头初始化升级
    if not user_tables:
        return

    # 2. 已由 Alembic 纳管的库：直接跳过纳管逻辑
    if "alembic_version" in raw_tables:
        return

    expected_0002_tables = set(Base.metadata.tables.keys())
    expected_0001_tables = expected_0002_tables - {"repository_members"}

    user_cols_0002_additions = {"email", "hashed_password", "is_active", "updated_at"}
    repo_cols_0002_additions = {"created_by", "is_public"}

    existing_columns = {t: {c["name"] for c in inspector.get_columns(t)} for t in user_tables}

    # 检查 0002 完整性
    missing_0002_tables = expected_0002_tables - user_tables
    missing_0002_columns: dict[str, set[str]] = {}
    if not missing_0002_tables:
        for t in expected_0002_tables:
            req_cols = {c.name for c in Base.metadata.tables[t].columns}
            missing = req_cols - existing_columns.get(t, set())
            if missing:
                missing_0002_columns[t] = missing

    missing_0002_fks = _missing_foreign_keys(inspector, expected_0002_tables & user_tables)
    is_full_0002 = not missing_0002_tables and not missing_0002_columns and not missing_0002_fks

    # 检查 0001 基线完整性
    is_baseline_0001 = False
    if "repository_members" not in user_tables and expected_0001_tables.issubset(user_tables):
        users_actual = existing_columns.get("users", set())
        users_0001_req = {
            c.name for c in Base.metadata.tables["users"].columns
        } - user_cols_0002_additions
        users_0001_ok = users_0001_req.issubset(
            users_actual
        ) and user_cols_0002_additions.isdisjoint(users_actual)

        repos_actual = existing_columns.get("repositories", set())
        repos_0001_req = {
            c.name for c in Base.metadata.tables["repositories"].columns
        } - repo_cols_0002_additions
        repos_0001_ok = repos_0001_req.issubset(
            repos_actual
        ) and repo_cols_0002_additions.isdisjoint(repos_actual)

        other_tables_ok = all(
            {c.name for c in Base.metadata.tables[t].columns}.issubset(
                existing_columns.get(t, set())
            )
            for t in expected_0001_tables
            if t not in ("users", "repositories")
        )

        missing_0001_fks = _missing_foreign_keys(
            inspector,
            expected_0001_tables,
            excluded_columns={"repositories": repo_cols_0002_additions},
        )
        is_baseline_0001 = (
            users_0001_ok and repos_0001_ok and other_tables_ok and not missing_0001_fks
        )

    if is_full_0002:
        logger.info("检测到结构完整且已包含认证隔离的存量数据库，自动标记版本为 head...")
        command.stamp(alembic_cfg, "head")
    elif is_baseline_0001:
        logger.info("检测到未迁移的完整存量基线数据库，自动标记版本为 0001_initial...")
        command.stamp(alembic_cfg, "0001_initial")
    else:
        reasons = []
        if missing_0002_tables:
            reasons.append(f"表缺失: {sorted(missing_0002_tables)}")
        if missing_0002_columns:
            for t, cols in sorted(missing_0002_columns.items()):
                reasons.append(f"{t} 列缺失: {sorted(cols)}")
        if missing_0002_fks:
            reasons.append(f"外键约束缺失或不匹配: {missing_0002_fks}")
        if "users" in user_tables:
            u_cols = existing_columns.get("users", set())
            overlap = u_cols & user_cols_0002_additions
            if overlap and not user_cols_0002_additions.issubset(u_cols):
                reasons.append(
                    f"users 认证扩展字段不完整（缺失: {sorted(user_cols_0002_additions - u_cols)}）"
                )
        if "repositories" in user_tables:
            r_cols = existing_columns.get("repositories", set())
            overlap = r_cols & repo_cols_0002_additions
            if overlap and not repo_cols_0002_additions.issubset(r_cols):
                reasons.append(
                    f"repositories 隔离扩展字段不完整（缺失: {sorted(repo_cols_0002_additions - r_cols)}）"
                )
        detail = "; ".join(reasons) if reasons else "非标准或混合状态存量结构"
        raise RuntimeError(
            f"检测到不完整的存量数据库结构（{detail}），无法安全自动纳管，拒绝启动以防止数据状态损坏。"
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
