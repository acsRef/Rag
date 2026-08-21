from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM chat provider: "siliconflow" (default) or "minimax"
    chat_provider: str = "siliconflow"

    # SiliconFlow (Chat + Vision + Embedding + Rerank)
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    chat_model: str = "deepseek-ai/DeepSeek-V3"
    intent_model: str = "deepseek-ai/DeepSeek-V3"  # 意图路由（轻量路由分类，用非推理 V3；R1 改为复杂查询规划用，见 rewrite_model）
    rewrite_model: str = "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B"  # 复杂查询的子问题拆解/依赖规划（推理模型，仅复杂时动用）
    vision_model: str = "Qwen/Qwen3-VL-8B-Instruct"  # 图片理解（多模态 Qwen-VL 8B，遵守[类型]分类约定；原 Qwen2.5-VL-7B 已在硅基流动下架）
    embedding_model: str = "Qwen/Qwen3-VL-Embedding-8B"
    embedding_dimension: int = 4096
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # MiniMax (备选，chat_provider="minimax" 时启用)
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimaxi.com/v1"
    minimax_model: str = "MiniMax-M3"

    # PostgreSQL
    database_url: str = "postgresql://ragent:ragent@localhost:5432/ragent"

    # JWT
    jwt_secret: str = "change-me-to-a-random-secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440  # 24h

    # 首次启动种子账号（CLAUDE.md 宣称可配，旧实现硬编码 admin/admin123）
    default_username: str = "admin"
    default_password: str = "admin123"

    # RAG params
    vector_search_top_k: int = 10
    rerank_top_k: int = 5
    # 复杂查询（多文档/多年度）放宽检索数量：让多个年份的 chunks 都能进入 prompt
    complex_rerank_top_k: int = 10
    intent_min_score: float = 0.35
    max_intent_count: int = 3
    max_sub_questions: int = 4   # rewrite 拆题上限（防 LLM 无限展开 + 重排/嵌入并发雪崩）
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

    # Embedding cache (Day 1 上午；app/core/cache.py::EmbeddingCache)
    embedding_cache_enabled: bool = True     # env: EMBEDDING_CACHE_ENABLED

    # Current embedding version (Day 2 上午)
    # 1 = 老 embedding（indexer 写 c.text 时计算）
    # 2 = build_embedding_text() 重 embed 后的版本
    # hybrid_search 加 AND embedding_version = :v 过滤；老 chunk 标 1，新 ingest + reembed_v2 标 2
    # 当前激活版本（Day 2 上午）。2026-08-21：reembed_v2.py 重跑（默认 chunk-only 模式）
    # 已把 1381 chunks 重建为 v1；切到 1 启用 chunk-only retrieval（baseline 验证：见
    # docs/plans/2026-08-23-day2-morning-done.md）。如需重做 v2 ablation，把环境变量
    # CURRENT_EMBEDDING_VERSION=2 + 跑 tools/reembed_v2.py --use-build-embedding-text --target-version 2。
    current_embedding_version: int = 1      # env: CURRENT_EMBEDDING_VERSION

    # Retrieval cache (Day 1 上午；app/core/cache.py::RetrievalCache)
    retrieval_cache_enabled: bool = True     # env: RETRIEVAL_CACHE_ENABLED

    # Strategy flags (Day 1 下午；plan §四.1)
    # 5 个检索层策略：Day 1 下午 baseline ablation（docs/plans/2026-08-23-baseline-ablation.md）
    # 证明在三一年报语料 on vs off recall@10 同为 100%，MRR 反向 +1.1pp——
    # 默认关掉，保留 env override 通道。需要时通过 CROSS_DOC_ENABLED=true 等单点打开。
    cross_doc_enabled: bool = False          # env: CROSS_DOC_ENABLED
    section_boost_enabled: bool = False      # env: SECTION_BOOST_ENABLED
    section_supplement_enabled: bool = False # env: SECTION_SUPPLEMENT_ENABLED
    year_supplement_enabled: bool = False    # env: YEAR_SUPPLEMENT_ENABLED
    query_decomposition_enabled: bool = False # env: QUERY_DECOMPOSITION_ENABLED
    evidence_gate_enabled: bool = False      # env: EVIDENCE_GATE_ENABLED (Day 2 接入)
    evidence_min_coverage: float = 0.7       # env: EVIDENCE_MIN_COVERAGE

    # Multi-channel retrieval: question embedding channel
    question_channel_enabled: bool = True       # env: QUESTION_CHANNEL_ENABLED
    question_channel_top_k: int = 10           # per-question vector search top_k
    question_channel_rrf_weight: float = 0.15  # RRF fusion weight (low to avoid noise)

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
