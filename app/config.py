from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # MiniMax M3
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimaxi.com/v1"
    minimax_model: str = "MiniMax-M3"

    # SiliconFlow (Embedding + Rerank)
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "Qwen/Qwen3-VL-Embedding-8B"
    embedding_dimension: int = 4096
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # PostgreSQL
    database_url: str = "postgresql://ragent:ragent@localhost:5432/ragent"

    # JWT
    jwt_secret: str = "change-me-to-a-random-secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440  # 24h

    # RAG params
    vector_search_top_k: int = 10
    rerank_top_k: int = 5
    intent_min_score: float = 0.35
    max_intent_count: int = 3
    hybrid_search_enabled: bool = True
    hybrid_search_top_k: int = 20  # 单路搜多取一些用于 RRF 合并
    hybrid_rrf_k: int = 60  # RRF 常数

    # MMR diversity rerank
    mmr_enabled: bool = True
    mmr_lambda: float = 0.7
    mmr_candidate_k: int = 30
    mmr_max_per_doc: int = 2
    mmr_doc_penalty: float = 0.05

    # Token budget — 控制注入 LLM 的 prompt 各部分大小
    # 默认值基于 MiniMax M3 128K 上下文,留足余量
    prompt_max_tokens: int = 10000      # 总预算(不含 LLM 输出预留)
    history_max_tokens: int = 2000      # 近期对话的 token 预算
    summary_max_tokens: int = 800       # 历史摘要的最大 token 数
    summary_trigger_tokens: int = 2000  # 旧消息累积超过此值时触发摘要
    chunks_max_tokens: int = 6000       # 检索 chunks 的最大 token 预算

    # PII / Sensitive data
    pii_enabled: bool = True
    pii_cache_ttl: int = 300
    pii_encryption_key: str = "change-me-to-a-random-key"

    # Upload
    max_upload_size_mb: int = 50

    # Chunker
    chunk_max_size: int = 2048

    # Embedding resilience
    embedding_max_retries: int = 3
    embedding_backoff_base: float = 1.0
    embedding_rate_limit_rps: int = 5

    # Circuit breaker
    circuit_breaker_enabled: bool = True     # env: CIRCUIT_BREAKER_ENABLED
    circuit_breaker_threshold: int = 10      # consecutive failures before OPEN
    circuit_breaker_cooldown: float = 30.0   # seconds before HALF_OPEN probe

    # Cross-doc relation
    cross_doc_embedding_threshold: float = 0.7  # doc embedding cosine threshold (channel 3)
    cross_doc_source_label: str = "来源"        # [来源: filename] label prefix

    # Degradation hints
    degradation_hint_enabled: bool = True    # env: DEGRADATION_HINT_ENABLED

    # Logging
    log_level: str = "INFO"                # env: LOG_LEVEL (DEBUG/INFO/WARNING/ERROR)
    log_dir: str = "logs"                  # env: LOG_DIR
    log_max_bytes: int = 10 * 1024 * 1024  # 10MB per file
    log_backup_count: int = 7              # 保留 7 个 backup 文件
    log_to_console: bool = True            # 同步输出 stderr 方便开发

    # Diagnostics
    diagnostics_enabled: bool = True       # env: DIAGNOSTICS_ENABLED
    diagnostics_dir: str = "diagnostics"   # env: DIAGNOSTICS_DIR
    diagnostics_max_index: int = 500       # env: DIAGNOSTICS_MAX_INDEX

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
