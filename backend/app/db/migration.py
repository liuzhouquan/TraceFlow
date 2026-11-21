import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.config import settings

logger = logging.getLogger(__name__)


def run_migrations(db_url: str | None = None) -> None:
    """运行 Alembic 数据库迁移升级到最新版本 (head)。"""
    backend_dir = Path(__file__).resolve().parent.parent.parent
    alembic_ini = backend_dir / "alembic.ini"
    if not alembic_ini.exists():
        logger.warning("未找到 alembic.ini，跳过数据库自动迁移。")
        return

    target_url = db_url or settings.database_url
    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("sqlalchemy.url", target_url)
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))

    logger.info("正在执行数据库版本迁移 (alembic upgrade head)...")
    command.upgrade(alembic_cfg, "head")
    logger.info("数据库版本迁移已完成。")
