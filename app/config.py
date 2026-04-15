from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    APP_ENV: Literal["development", "staging", "production"] = Field(default="development")
    DEBUG: bool = Field(default=True)

    # API 外部访问地址（用于生成媒体文件 URL）
    API_BASE_URL: str | None = Field(default="http://localhost:8000")

    DATABASE_URL: str | None = Field(default=None)
    REDIS_URL: str | None = Field(default=None)

    JWT_SECRET: str | None = Field(default=None)
    JWT_ALGORITHM: str | None = Field(default=None)

    # Auth Service (统一认证)
    AUTH_SERVICE_URL: str = Field(default="http://localhost:8100")
    AUTH_SERVICE_JWKS_URL: str | None = Field(default=None)

    CONFIG_CENTER_DB_ENABLED: bool = Field(default=True)
    CONFIG_CENTER_CACHE_TTL: int = Field(default=60)
    # 用户默认免费 ASR 额度（秒），1小时 = 3600秒
    DEFAULT_USER_FREE_QUOTA_SECONDS: int = Field(default=3600)

    MINIO_ENDPOINT: str | None = Field(default=None)
    MINIO_ACCESS_KEY: str | None = Field(default=None)
    MINIO_SECRET_KEY: str | None = Field(default=None)
    MINIO_BUCKET: str | None = Field(default=None)
    MINIO_USE_SSL: bool | None = Field(default=None)

    COS_REGION: str | None = Field(default=None)
    COS_BUCKET: str | None = Field(default=None)
    COS_SECRET_ID: str | None = Field(default=None)
    COS_SECRET_KEY: str | None = Field(default=None)
    COS_ENDPOINT: str | None = Field(default=None)
    COS_USE_SSL: bool | None = Field(default=True)
    COS_PUBLIC_READ: bool | None = Field(default=False)

    OSS_ENDPOINT: str | None = Field(default=None)
    OSS_REGION: str | None = Field(default=None)
    OSS_BUCKET: str | None = Field(default=None)
    OSS_USE_SSL: bool | None = Field(default=True)

    TOS_ENDPOINT: str | None = Field(default=None)
    TOS_REGION: str | None = Field(default=None)
    TOS_BUCKET: str | None = Field(default=None)
    TOS_ACCESS_KEY: str | None = Field(default=None)
    TOS_SECRET_KEY: str | None = Field(default=None)

    TENCENT_SECRET_ID: str | None = Field(default=None)
    TENCENT_SECRET_KEY: str | None = Field(default=None)
    TENCENT_REGION: str | None = Field(default=None)
    TENCENT_ASR_APP_ID: str | None = Field(default=None)
    TENCENT_ASR_ENGINE_MODEL_TYPE: str | None = Field(default=None)
    TENCENT_ASR_ENGINE_MODEL_TYPE_FILE_FAST: str | None = Field(default=None)
    TENCENT_ASR_CHANNEL_NUM: int | None = Field(default=None)
    TENCENT_ASR_SOURCE_TYPE: int | None = Field(default=None)
    TENCENT_ASR_RES_TEXT_FORMAT: int | None = Field(default=None)
    TENCENT_ASR_SPEAKER_DIA: int | None = Field(default=None)
    TENCENT_ASR_SPEAKER_NUMBER: int | None = Field(default=None)
    TENCENT_ASR_POLL_INTERVAL: int | None = Field(default=None)
    TENCENT_ASR_MAX_WAIT_SECONDS: int | None = Field(default=None)
    ALIYUN_ACCESS_KEY_ID: str | None = Field(default=None)
    ALIYUN_ACCESS_KEY_SECRET: str | None = Field(default=None)
    ALIYUN_NLS_APP_KEY: str | None = Field(default=None)
    VOLC_ASR_APP_ID: str | None = Field(default=None)
    VOLC_ASR_ACCESS_TOKEN: str | None = Field(default=None)
    VOLC_ASR_RESOURCE_ID: str | None = Field(default=None)
    VOLC_ASR_MODEL_NAME: str | None = Field(default=None)
    VOLC_ASR_MODEL_VERSION: str | None = Field(default=None)
    VOLC_ASR_LANGUAGE: str | None = Field(default=None)
    VOLC_ASR_ENABLE_ITN: bool | None = Field(default=None)
    VOLC_ASR_SHOW_UTTERANCES: bool | None = Field(default=None)
    VOLC_ASR_POLL_INTERVAL: int | None = Field(default=None)
    VOLC_ASR_MAX_WAIT_SECONDS: int | None = Field(default=None)

    DOUBAO_API_KEY: str | None = Field(default=None)
    DOUBAO_BASE_URL: str | None = Field(default=None)
    DOUBAO_MODEL: str | None = Field(default=None)
    DOUBAO_MAX_TOKENS: int | None = Field(default=None)
    QWEN_API_KEY: str | None = Field(default=None)
    QWEN_MODEL: str | None = Field(default=None)
    MOONSHOT_API_KEY: str | None = Field(default=None)
    MOONSHOT_BASE_URL: str | None = Field(default="https://api.moonshot.cn/v1")
    MOONSHOT_MODEL: str | None = Field(default="moonshot-v1-8k")
    MOONSHOT_MAX_TOKENS: int | None = Field(default=4096)
    DEEPSEEK_API_KEY: str | None = Field(default=None)
    DEEPSEEK_BASE_URL: str | None = Field(default="https://api.deepseek.com")
    DEEPSEEK_MODEL: str | None = Field(default="deepseek-chat")
    DEEPSEEK_MAX_TOKENS: int | None = Field(default=4096)
    OPENROUTER_API_KEY: str | None = Field(default=None)
    OPENROUTER_BASE_URL: str | None = Field(default="https://openrouter.ai/api/v1")
    OPENROUTER_MODEL: str | None = Field(default=None)
    OPENROUTER_MAX_TOKENS: int | None = Field(default=4096)
    OPENROUTER_HTTP_REFERER: str | None = Field(default=None)
    OPENROUTER_APP_TITLE: str | None = Field(default=None)
    OPENROUTER_DYNAMIC_MODELS: bool = Field(default=False)

    # LiteLLM Proxy
    LITELLM_BASE_URL: str = Field(default="http://litellm-proxy:4000")
    LITELLM_API_KEY: str | None = Field(default=None)
    LITELLM_MODEL: str = Field(default="deepseek/deepseek-chat")
    LITELLM_MAX_TOKENS: int = Field(default=4096)

    OPENAI_API_KEY: str | None = Field(default=None)
    OPENAI_BASE_URL: str | None = Field(default="https://api.openai.com/v1")

    RAG_EMBEDDING_ENABLED: bool = Field(default=True)
    RAG_EMBEDDING_PROVIDER: str | None = Field(default="openrouter")
    RAG_EMBEDDING_MODEL: str | None = Field(default="text-embedding-3-small")
    RAG_EMBEDDING_DIM: int | None = Field(default=1536)
    RAG_CHUNK_SIZE: int = Field(default=300)
    RAG_CHUNK_OVERLAP: int = Field(default=50)
    RAG_EMBED_BATCH_SIZE: int = Field(default=64)

    UPLOAD_ALLOWED_EXTENSIONS: str | None = Field(default=None)
    UPLOAD_MAX_SIZE_BYTES: int | None = Field(default=None)
    UPLOAD_PRESIGN_EXPIRES: int | None = Field(default=None)

    YOUTUBE_DOWNLOAD_DIR: str | None = Field(default=None)
    YOUTUBE_OUTPUT_TEMPLATE: str | None = Field(default=None)
    YOUTUBE_DOWNLOAD_FORMAT: str | None = Field(default=None)

    # Google OAuth for YouTube API
    GOOGLE_CLIENT_ID: str | None = Field(default=None)
    GOOGLE_CLIENT_SECRET: str | None = Field(default=None)
    YOUTUBE_OAUTH_REDIRECT_URI: str | None = Field(default=None)
    FRONTEND_URL: str = Field(default="http://localhost:3000")

    # CORS 允许的额外 origins（逗号分隔）
    CORS_ORIGINS: str | None = Field(default=None)

    STATS_CURRENCY: str = Field(default="CNY")

    TASK_CLEANUP_DELAY_SECONDS: int = Field(default=300)

    # PromptHub
    PROMPTHUB_BASE_URL: str | None = Field(default=None)
    PROMPTHUB_API_KEY: str | None = Field(default=None)
    PROMPTHUB_CACHE_TTL: int = Field(default=300)  # seconds
    PROMPTHUB_IMAGE_GEN_PROJECT_ID: str | None = Field(default=None)


settings = Settings()
