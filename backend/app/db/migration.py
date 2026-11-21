import logging
import os
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import settings

logger = logging.getLogger(__name__)


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
    try:
        if bind is not None:
            inspector = inspect(bind)
            table_names = set(inspector.get_table_names())
            if "users" in table_names and "alembic_version" not in table_names:
                user_cols = {c["name"] for c in inspector.get_columns("users")}
                if "email" in user_cols and "hashed_password" in user_cols:
                    logger.info("检测到已包含认证字段的存量数据库，自动标记版本为 head...")
                    command.stamp(alembic_cfg, "head")
                else:
                    logger.info("检测到未迁移的存量基线数据库，自动标记版本为 0001_initial...")
                    command.stamp(alembic_cfg, "0001_initial")
        else:
            check_engine = create_engine(target_url)
            with check_engine.connect() as conn:
                inspector = inspect(conn)
                table_names = set(inspector.get_table_names())
                if "users" in table_names and "alembic_version" not in table_names:
                    user_cols = {c["name"] for c in inspector.get_columns("users")}
                    if "email" in user_cols and "hashed_password" in user_cols:
                        logger.info("检测到已包含认证字段的存量数据库，自动标记版本为 head...")
                        command.stamp(alembic_cfg, "head")
                    else:
                        logger.info("检测到未迁移的存量基线数据库，自动标记版本为 0001_initial...")
                        command.stamp(alembic_cfg, "0001_initial")
            check_engine.dispose()
    except Exception as exc:
        logger.warning(f"存量数据库版本探测跳过: {exc}")

    logger.info("正在执行数据库版本迁移 (alembic upgrade head)...")
    command.upgrade(alembic_cfg, "head")
    logger.info("数据库版本迁移已完成。")
