import pytest
from pathlib import Path
from alembic.config import Config
from alembic import command
from sqlalchemy import create_engine, inspect


def test_alembic_migrations_upgrade_and_downgrade(tmp_path):
    backend_dir = Path(__file__).resolve().parent.parent.parent
    alembic_ini = backend_dir / "alembic.ini"
    assert alembic_ini.exists(), "alembic.ini 必须存在"

    test_db = tmp_path / "migration_test.db"
    db_url = f"sqlite+pysqlite:///{test_db}"

    # 配置 Alembic Config
    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))

    engine = create_engine(db_url)
    try:
        # 1. 执行全部升级到 head
        command.upgrade(alembic_cfg, "head")

        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "users" in tables
        assert "repositories" in tables
        assert "workspaces" in tables
        assert "repository_members" in tables

        # 检查 users 表新增字段
        user_cols = {col["name"] for col in inspector.get_columns("users")}
        assert "email" in user_cols
        assert "hashed_password" in user_cols
        assert "is_active" in user_cols

        # 检查 repositories 表新增字段
        repo_cols = {col["name"] for col in inspector.get_columns("repositories")}
        assert "created_by" in repo_cols
        assert "is_public" in repo_cols

        # 2. 执行降级 1 个版本 (到 baseline 0001)
        command.downgrade(alembic_cfg, "-1")
        inspector = inspect(engine)
        tables_after_down = set(inspector.get_table_names())
        assert "repository_members" not in tables_after_down

        # 3. 再次升级到 head，验证幂等性
        command.upgrade(alembic_cfg, "head")
        inspector = inspect(engine)
        assert "repository_members" in set(inspector.get_table_names())
    finally:
        engine.dispose()
