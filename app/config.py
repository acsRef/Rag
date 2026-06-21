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
    history_keep_turns: int = 4
    history_summary_turns: int = 10  # 多少轮之前的消息触发摘要压缩
    max_summary_tokens: int = 800    # 摘要最大 token 数（超出时再压缩）

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
