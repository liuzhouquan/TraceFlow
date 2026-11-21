import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.models import Base
from app import main
from app.db import session
from app.services.rag import vector_store


@pytest.mark.asyncio
async def test_startup_preserves_legacy_embedding_data(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE documents ADD COLUMN embedding JSON"))
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(session, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(settings, "auto_sync_enabled", False)
    monkeypatch.setattr(vector_store, "milvus_runtime_status", lambda: {})
    monkeypatch.setattr(vector_store, "delete_milvus_documents", lambda **kwargs: None)
    app = main.create_app()
    try:
        await app.router.startup()
        assert "embedding" in {col["name"] for col in inspect(engine).get_columns("documents")}
    finally:
        await app.router.shutdown()
        engine.dispose()
