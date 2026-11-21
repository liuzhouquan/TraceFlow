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


def test_migrated_schema_matches_orm_metadata(tmp_path):
    from app.db.models import Base

    backend_dir = Path(__file__).resolve().parent.parent.parent
    alembic_ini = backend_dir / "alembic.ini"
    test_db = tmp_path / "schema_match_test.db"
    db_url = f"sqlite+pysqlite:///{test_db}"

    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))

    # 执行迁移
    command.upgrade(alembic_cfg, "head")

    mig_engine = create_engine(db_url)
    mig_insp = inspect(mig_engine)
    mig_tables = set(mig_insp.get_table_names()) - {"alembic_version"}

    mem_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(mem_engine)
    orm_insp = inspect(mem_engine)
    orm_tables = set(orm_insp.get_table_names())

    # 验证表一致性
    assert orm_tables == mig_tables, f"表不匹配: {orm_tables ^ mig_tables}"

    # 逐表比对列
    for table in sorted(orm_tables):
        mig_cols = {c["name"] for c in mig_insp.get_columns(table)}
        orm_cols = {c["name"] for c in orm_insp.get_columns(table)}
        assert orm_cols == mig_cols, (
            f"表 {table} 列不一致: 缺失={orm_cols - mig_cols}, 多余={mig_cols - orm_cols}"
        )

    # 特别检查曾发生漂移的字段
    wf_cols = {c["name"] for c in mig_insp.get_columns("workflow_runs")}
    assert "logs_text" in wf_cols

    mig_engine.dispose()
    mem_engine.dispose()


def test_legacy_database_adoption_by_run_migrations(tmp_path):
    from app.db.models import Base
    from app.db.migration import run_migrations

    test_db = tmp_path / "legacy_test.db"
    db_url = f"sqlite+pysqlite:///{test_db}"

    # 1. 模拟存量老数据库：直接通过 create_all 创建表结构，但没有 alembic_version 表
    legacy_engine = create_engine(db_url)
    Base.metadata.create_all(legacy_engine)
    inspector = inspect(legacy_engine)
    assert "users" in set(inspector.get_table_names())
    assert "alembic_version" not in set(inspector.get_table_names())
    legacy_engine.dispose()

    # 2. 调用 run_migrations 自动纳管存量库，不得抛出 OperationalError: table users already exists
    run_migrations(db_url)

    # 3. 验证已被成功纳管并标记了版本
    mig_engine = create_engine(db_url)
    insp = inspect(mig_engine)
    assert "alembic_version" in set(insp.get_table_names())
    mig_engine.dispose()
