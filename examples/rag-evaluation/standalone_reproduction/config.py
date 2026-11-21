from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_EMBEDDING_QUERY_INSTRUCTION = (
    "Given a user question, retrieve relevant passages from the knowledge base "
    "that answer the question"
)

# Pass the exact path so this standalone demo never walks up to TraceFlow's root .env.
load_dotenv(BASE_DIR / ".env", override=False)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true 或 false")


def _local_path(name: str, default: str) -> Path:
    raw_value = os.getenv(name, default).strip() or default
    value = Path(raw_value)
    return value if value.is_absolute() else BASE_DIR / value


@dataclass(frozen=True)
class Settings:
    provider: str
    base_url: str
    api_key: str
    embedding_model: str
    chat_model: str
    knowledge_base_name: str
    chunk_size: int
    chunk_overlap: int
    top_k: int
    min_relevance_score: float
    max_upload_bytes: int
    model_timeout_seconds: int
    embedding_dimensions: int
    embedding_batch_size: int
    catalog_path: Path
    upload_dir: Path
    milvus_uri: str
    milvus_token: str
    milvus_database: str
    milvus_collection: str
    milvus_timeout_seconds: int
    milvus_write_batch_size: int
    milvus_collection_prefix: str = "rag_studio_production_v"
    retrieval_mode: str = "hybrid_rerank"
    dense_candidate_k: int = 20
    keyword_candidate_k: int = 20
    rerank_candidate_k: int = 20
    rrf_k: int = 60
    rerank_provider: str = "qwen_api"
    rerank_base_url: str = "https://dashscope.aliyuncs.com/compatible-api/v1"
    rerank_api_key: str = ""
    rerank_model: str = "qwen3-rerank"
    rerank_instruction: str = (
        "Given a user question, rank passages by how well they contain the facts "
        "needed to answer it."
    )
    rerank_timeout_seconds: int = 60
    rerank_min_score: float = 0.15
    context_chunk_max_chars: int = 900
    embedding_query_instruction: str | None = DEFAULT_EMBEDDING_QUERY_INSTRUCTION
    index_schema_version: str = "production-rag-v3"
    quality_recall_at_k_min: float = 1.0
    quality_mrr_min: float = 0.75
    quality_ndcg_at_k_min: float = 0.75
    quality_negative_rejection_min: float = 1.0
    quality_regression_tolerance: float = 0.02
    release_top_k: int = 4
    investigation_max_rounds: int = 2
    investigation_max_queries: int = 3
    ragas_enabled: bool = False
    ragas_evaluation_set: Path = BASE_DIR / "eval_cases" / "held_out_v1.jsonl"
    ragas_judge_base_url: str = ""
    ragas_judge_api_key: str = ""
    ragas_judge_model: str = ""
    ragas_prompt_version: str = "ragas-0.4.3-default"
    ragas_input_language: str = "zh-CN"
    ragas_answer_relevancy_strictness: int = 1
    ragas_timeout_seconds: int = 180
    ragas_judge_max_tokens: int = 8192
    ragas_context_precision_min: float = 0.80
    ragas_context_recall_min: float = 0.85
    ragas_faithfulness_min: float = 0.90
    ragas_answer_relevancy_min: float = 0.75


def load_settings() -> Settings:
    provider = os.getenv("MODEL_PROVIDER", os.getenv("EMBEDDING_PROVIDER", "deterministic")).strip().lower()
    default_base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    chunk_size = _env_int("CHUNK_SIZE", 1000)
    chunk_overlap = _env_int("CHUNK_OVERLAP", 80)
    embedding_dimensions = _env_int(
        "EMBEDDING_DIMENSIONS",
        384 if provider == "deterministic" else 1024,
    )
    query_instruction = os.getenv(
        "EMBEDDING_QUERY_INSTRUCTION",
        DEFAULT_EMBEDDING_QUERY_INSTRUCTION,
    ).strip()
    milvus_collection = (
        os.getenv("MILVUS_COLLECTION", "").strip()
        or "rag_studio_production_active"
    )
    milvus_collection_prefix = (
        os.getenv("MILVUS_COLLECTION_PREFIX", "").strip()
        or "rag_studio_production_v"
    )
    retrieval_mode = os.getenv("RETRIEVAL_MODE", "hybrid_rerank").strip().lower()
    rerank_provider = os.getenv("RERANK_PROVIDER", "qwen_api").strip().lower()
    if provider not in {"deterministic", "ollama", "openai_compatible"}:
        raise ValueError("MODEL_PROVIDER 只支持 deterministic、ollama 或 openai_compatible")
    if retrieval_mode not in {"dense", "hybrid", "hybrid_rerank"}:
        raise ValueError("RETRIEVAL_MODE 只支持 dense、hybrid 或 hybrid_rerank")
    if rerank_provider not in {"qwen_api", "heuristic", "disabled"}:
        raise ValueError("RERANK_PROVIDER 只支持 qwen_api、heuristic 或 disabled")
    if chunk_size < 200:
        raise ValueError("CHUNK_SIZE 不能小于 200")
    if chunk_size > 16_000:
        raise ValueError("CHUNK_SIZE 不能大于 16000")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("CHUNK_OVERLAP 必须大于等于 0 且小于 CHUNK_SIZE")
    if embedding_dimensions < 2 or embedding_dimensions > 32_768:
        raise ValueError("EMBEDDING_DIMENSIONS 必须在 2 到 32768 之间")
    if provider == "deterministic" and embedding_dimensions != 384:
        raise ValueError("deterministic 模式的 EMBEDDING_DIMENSIONS 必须是 384")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,254}", milvus_collection):
        raise ValueError("MILVUS_COLLECTION 只能包含字母、数字和下划线，且不能以数字开头")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,220}", milvus_collection_prefix):
        raise ValueError("MILVUS_COLLECTION_PREFIX 只能包含字母、数字和下划线，且不能以数字开头")
    if milvus_collection == milvus_collection_prefix:
        raise ValueError("MILVUS_COLLECTION 不能与 MILVUS_COLLECTION_PREFIX 相同")

    settings = Settings(
        provider=provider,
        base_url=os.getenv("MODEL_BASE_URL", default_base_url).rstrip("/"),
        api_key=os.getenv("MODEL_API_KEY", ""),
        embedding_model=os.getenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b"),
        chat_model=os.getenv("CHAT_MODEL", "qwen3:4b-instruct"),
        knowledge_base_name=os.getenv("KNOWLEDGE_BASE_NAME", "第 9 章 Production RAG 工作室"),
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        top_k=max(1, min(_env_int("TOP_K", 4), 20)),
        min_relevance_score=max(-1.0, min(_env_float("MIN_RELEVANCE_SCORE", 0.35), 1.0)),
        max_upload_bytes=max(1, _env_int("MAX_UPLOAD_MB", 8)) * 1024 * 1024,
        model_timeout_seconds=max(5, _env_int("MODEL_TIMEOUT_SECONDS", 120)),
        embedding_dimensions=embedding_dimensions,
        embedding_batch_size=max(1, min(_env_int("EMBEDDING_BATCH_SIZE", 16), 128)),
        catalog_path=_local_path("CATALOG_PATH", "data/rag_studio_production.sqlite3"),
        upload_dir=_local_path("UPLOAD_DIR", "data/uploads"),
        milvus_uri=os.getenv("MILVUS_URI", "http://127.0.0.1:19530").strip(),
        milvus_token=os.getenv("MILVUS_TOKEN", "").strip(),
        milvus_database=os.getenv("MILVUS_DATABASE", "default").strip() or "default",
        milvus_collection=milvus_collection,
        milvus_collection_prefix=milvus_collection_prefix,
        milvus_timeout_seconds=max(
            1,
            min(_env_int("MILVUS_TIMEOUT_SECONDS", 10), 300),
        ),
        milvus_write_batch_size=max(
            1,
            min(_env_int("MILVUS_WRITE_BATCH_SIZE", 64), 1000),
        ),
        retrieval_mode=retrieval_mode,
        dense_candidate_k=max(1, min(_env_int("DENSE_CANDIDATE_K", 20), 100)),
        keyword_candidate_k=max(1, min(_env_int("KEYWORD_CANDIDATE_K", 20), 100)),
        rerank_candidate_k=max(1, min(_env_int("RERANK_CANDIDATE_K", 20), 100)),
        rrf_k=max(1, min(_env_int("RRF_K", 60), 16_383)),
        rerank_provider=rerank_provider,
        rerank_base_url=os.getenv(
            "RERANK_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-api/v1",
        ).rstrip("/"),
        rerank_api_key=os.getenv(
            "RERANK_API_KEY",
            os.getenv("DASHSCOPE_API_KEY", ""),
        ).strip(),
        rerank_model=os.getenv("RERANK_MODEL", "qwen3-rerank").strip(),
        rerank_instruction=os.getenv(
            "RERANK_INSTRUCTION",
            "Given a user question, rank passages by how well they contain the facts needed to answer it.",
        ).strip(),
        rerank_timeout_seconds=max(
            3,
            min(_env_int("RERANK_TIMEOUT_SECONDS", 60), 300),
        ),
        rerank_min_score=max(
            0.0,
            min(_env_float("RERANK_MIN_SCORE", 0.15), 1.0),
        ),
        context_chunk_max_chars=max(
            200,
            min(_env_int("CONTEXT_CHUNK_MAX_CHARS", 900), 4000),
        ),
        embedding_query_instruction=query_instruction or None,
        index_schema_version=os.getenv("INDEX_SCHEMA_VERSION", "production-rag-v3").strip()
        or "production-rag-v3",
        quality_recall_at_k_min=max(
            0.0, min(_env_float("QUALITY_RECALL_AT_K_MIN", 1.0), 1.0)
        ),
        quality_mrr_min=max(0.0, min(_env_float("QUALITY_MRR_MIN", 0.75), 1.0)),
        quality_ndcg_at_k_min=max(
            0.0, min(_env_float("QUALITY_NDCG_AT_K_MIN", 0.75), 1.0)
        ),
        quality_negative_rejection_min=max(
            0.0,
            min(_env_float("QUALITY_NEGATIVE_REJECTION_MIN", 1.0), 1.0),
        ),
        quality_regression_tolerance=max(
            0.0,
            min(_env_float("QUALITY_REGRESSION_TOLERANCE", 0.02), 1.0),
        ),
        release_top_k=max(1, min(_env_int("RELEASE_TOP_K", 4), 20)),
        investigation_max_rounds=max(
            1, min(_env_int("INVESTIGATION_MAX_ROUNDS", 2), 5)
        ),
        investigation_max_queries=max(
            1, min(_env_int("INVESTIGATION_MAX_QUERIES", 3), 10)
        ),
        ragas_enabled=_env_bool("RAGAS_ENABLED", False),
        ragas_evaluation_set=_local_path(
            "RAGAS_EVALUATION_SET",
            "eval_cases/held_out_v1.jsonl",
        ),
        ragas_judge_base_url=os.getenv(
            "RAGAS_JUDGE_BASE_URL",
            os.getenv("MODEL_BASE_URL", default_base_url),
        ).rstrip("/"),
        ragas_judge_api_key=os.getenv(
            "RAGAS_JUDGE_API_KEY",
            os.getenv("MODEL_API_KEY", ""),
        ).strip(),
        ragas_judge_model=os.getenv(
            "RAGAS_JUDGE_MODEL",
            os.getenv("CHAT_MODEL", "qwen3:4b-instruct"),
        ).strip(),
        ragas_prompt_version=os.getenv(
            "RAGAS_PROMPT_VERSION", "ragas-0.4.3-default"
        ).strip(),
        ragas_input_language=os.getenv("RAGAS_INPUT_LANGUAGE", "zh-CN").strip(),
        ragas_answer_relevancy_strictness=max(
            1,
            min(_env_int("RAGAS_ANSWER_RELEVANCY_STRICTNESS", 1), 5),
        ),
        ragas_timeout_seconds=max(
            30,
            min(_env_int("RAGAS_TIMEOUT_SECONDS", 180), 1800),
        ),
        ragas_judge_max_tokens=max(
            512,
            min(_env_int("RAGAS_JUDGE_MAX_TOKENS", 8192), 8192),
        ),
        ragas_context_precision_min=max(
            0.0,
            min(_env_float("RAGAS_CONTEXT_PRECISION_MIN", 0.80), 1.0),
        ),
        ragas_context_recall_min=max(
            0.0,
            min(_env_float("RAGAS_CONTEXT_RECALL_MIN", 0.85), 1.0),
        ),
        ragas_faithfulness_min=max(
            0.0,
            min(_env_float("RAGAS_FAITHFULNESS_MIN", 0.90), 1.0),
        ),
        ragas_answer_relevancy_min=max(
            0.0,
            min(_env_float("RAGAS_ANSWER_RELEVANCY_MIN", 0.75), 1.0),
        ),
    )
    if not settings.milvus_uri:
        raise ValueError("MILVUS_URI 不能为空")
    if settings.ragas_enabled:
        if not settings.ragas_evaluation_set.is_file():
            raise ValueError(
                f"RAGAS_EVALUATION_SET 不存在：{settings.ragas_evaluation_set}"
            )
        if not settings.ragas_judge_base_url or not settings.ragas_judge_model:
            raise ValueError("启用 RAGAS 时必须配置 Judge Base URL 和模型")
    settings.catalog_path.parent.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings
