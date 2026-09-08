from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Lightweight OnCall Agent"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_log_level: str = "INFO"
    database_url: str = "sqlite+aiosqlite:///./data/oncall.db"
    vector_db_uri: str = "./data/milvus.db"
    upload_dir: str = "./data/uploads"
    model_cache_dir: str = "./data/model_cache"
    model_local_files_only: bool = True
    model_warmup: bool = True
    llm_provider: str = "deterministic"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b"
    llm_timeout_seconds: int = 60
    embedding_provider: str = "hashing"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_device: str = "cpu"
    embedding_dimension: int = 512
    embedding_query_instruction: str = "为这个句子生成表示以用于检索相关文章："
    reranker_provider: str = "cross_encoder"
    reranker_model: str = "BAAI/bge-reranker-base"
    reranker_device: str = "cpu"
    reranker_batch_size: int = 4
    chat_max_iterations: int = 8
    ops_max_iterations: int = 12
    tool_timeout_seconds: int = 30
    task_timeout_seconds: int = 600
    memory_recent_messages: int = 8
    memory_summary_threshold: int = 12
    memory_recall_messages: int = 3
    rag_top_k: int = 12
    rag_top_n: int = 5
    rag_retrieval_mode: str = "hybrid"
    rag_candidate_pool: int = 256
    rag_rrf_k: int = 60
    rag_bm25_k1: float = 1.5
    rag_bm25_b: float = 0.75
    rag_max_chunks_per_document: int = 2
    max_upload_mb: int = 10
    mcp_servers: str = "[]"
    mock_notification_allowlist: str = "oncall-demo"

    def ensure_directories(self) -> None:
        Path(self.upload_dir).mkdir(parents=True, exist_ok=True)
        Path(self.model_cache_dir).mkdir(parents=True, exist_ok=True)
        Path(self.vector_db_uri).parent.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite"):
            value = self.database_url.rsplit("///", 1)[-1]
            Path(value).parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
