import pytest
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


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


def test_migrated_schema_fk_parity_and_enforcement(tmp_path):
    import uuid
    from sqlalchemy import text, exc
    from app.db.models import Base

    backend_dir = Path(__file__).resolve().parent.parent.parent
    alembic_ini = backend_dir / "alembic.ini"
    test_db = tmp_path / "fk_parity_test.db"
    db_url = f"sqlite+pysqlite:///{test_db}"

    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))

    # 1. 执行全部升级到 head
    command.upgrade(alembic_cfg, "head")

    mig_engine = create_engine(db_url)
    mig_insp = inspect(mig_engine)

    mem_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(mem_engine)
    orm_insp = inspect(mem_engine)

    # 2. 比对外键约束总数及逐表一致性
    mig_total_fks = 0
    orm_total_fks = 0
    for table in sorted(set(orm_insp.get_table_names())):
        mig_fks = mig_insp.get_foreign_keys(table)
        orm_fks = orm_insp.get_foreign_keys(table)
        mig_total_fks += len(mig_fks)
        orm_total_fks += len(orm_fks)
        assert len(mig_fks) == len(orm_fks), (
            f"表 {table} 外键数量不匹配: mig={len(mig_fks)}, orm={len(orm_fks)}"
        )

    assert mig_total_fks == orm_total_fks == 49

    # 3. 验证 SQLite 开启外键后拒绝孤立 WorkflowRun 插入
    with mig_engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))
        orphan_id = str(uuid.uuid4())
        fake_repo = str(uuid.uuid4())
        with pytest.raises(exc.IntegrityError):
            conn.execute(
                text(
                    f"INSERT INTO workflow_runs (id, repo_id, name, status, jobs) "
                    f"VALUES ('{orphan_id}', '{fake_repo}', 'orphan_run', 'completed', '{{}}')"
                )
            )
            conn.commit()

    mig_engine.dispose()
    mem_engine.dispose()


def test_legacy_database_adoption_by_run_migrations(tmp_path):
    from app.db.models import Base
    from app.db.migration import run_migrations

    test_db = tmp_path / "legacy_test.db"
    db_url = f"sqlite+pysqlite:///{test_db}"

    # 1. 模拟完整 0002 存量老数据库：直接通过 create_all 创建表结构，但没有 alembic_version 表
    legacy_engine = create_engine(db_url)
    Base.metadata.create_all(legacy_engine)
    inspector = inspect(legacy_engine)
    assert "users" in set(inspector.get_table_names())
    assert "repository_members" in set(inspector.get_table_names())
    assert "alembic_version" not in set(inspector.get_table_names())
    legacy_engine.dispose()

    # 2. 调用 run_migrations 自动纳管存量库，不得抛出 OperationalError: table users already exists
    run_migrations(db_url)

    # 3. 验证已被成功纳管并标记了版本为 head
    mig_engine = create_engine(db_url)
    insp = inspect(mig_engine)
    assert "alembic_version" in set(insp.get_table_names())
    with mig_engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == "0002_auth_and_isolation"
    mig_engine.dispose()


def test_partial_legacy_database_adoption_rejected(tmp_path):
    from app.db.migration import run_migrations

    test_db = tmp_path / "partial_legacy.db"
    db_url = f"sqlite+pysqlite:///{test_db}"

    # 模拟残缺的存量老数据库：含有 users.email，但缺失 repository_members 表和 repositories 字段
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200), role VARCHAR(80), email VARCHAR(255), hashed_password VARCHAR(255))"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE repositories (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200), full_name VARCHAR(400))"
            )
        )
    engine.dispose()

    # 验证不完整的残缺老数据库被严格拒绝 (Fail-Closed)，拒绝静默标记 head
    with pytest.raises(RuntimeError, match="检测到不完整的存量数据库结构"):
        run_migrations(db_url)


def test_baseline_legacy_database_adoption_and_upgrade(tmp_path):
    from app.db.migration import run_migrations

    test_db = tmp_path / "baseline_legacy.db"
    db_url = f"sqlite+pysqlite:///{test_db}"

    # 模拟纯 0001 基线存量数据库（无 email、无 repository_members）
    backend_dir = Path(__file__).resolve().parent.parent.parent
    alembic_ini = backend_dir / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    command.upgrade(cfg, "0001_initial")

    # 模拟丢失 alembic_version 表（纯原生未接入迁移管理的老库）
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
    engine.dispose()

    # 调用 run_migrations：应自动探测为 0001_initial，并平滑升级至 head
    run_migrations(db_url)

    mig_engine = create_engine(db_url)
    insp = inspect(mig_engine)
    assert "alembic_version" in set(insp.get_table_names())
    assert "repository_members" in set(insp.get_table_names())
    user_cols = {c["name"] for c in insp.get_columns("users")}
    assert "email" in user_cols
    assert "is_active" in user_cols
    with mig_engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == "0002_auth_and_isolation"
    mig_engine.dispose()
