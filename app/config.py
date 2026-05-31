from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
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

    # 静态数据字段级加密密钥（Fernet）。用于加密落库的密钥/令牌（如 OAuth token）。
    # 生成：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # 支持逗号分隔多把密钥以便轮换（第一把加密，全部用于解密）。生产环境必须设置。
    FIELD_ENCRYPTION_KEY: str | None = Field(default=None)

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

    # OpenRouter 仅保留给 RAG embedding（embedder 仍直连 openrouter
    # 的 text-embedding-3-small；切到 LiteLLM 是 Phase 3.5 的事）。
    # 其它 LLM provider（doubao/qwen/moonshot/deepseek）的 env 已下线，
    # 全部由 LiteLLM Proxy 负责路由。
    OPENROUTER_API_KEY: str | None = Field(default=None)
    OPENROUTER_BASE_URL: str | None = Field(default="https://openrouter.ai/api/v1")
    OPENROUTER_HTTP_REFERER: str | None = Field(default=None)
    OPENROUTER_APP_TITLE: str | None = Field(default=None)

    # LiteLLM Proxy
    LITELLM_BASE_URL: str = Field(default="http://litellm-proxy:4000")
    LITELLM_API_KEY: str | None = Field(default=None)
    LITELLM_MODEL: str = Field(default="chat-default")
    LITELLM_MAX_TOKENS: int = Field(default=4096)

    # 远程 image-service（独立部署的 Gemini 生图服务）
    IMAGE_SERVICE_BASE_URL: str | None = Field(default=None)
    IMAGE_SERVICE_API_KEY: str | None = Field(default=None)
    IMAGE_SERVICE_DEFAULT_MODEL: str = Field(default="gemini-3-pro-image-preview")

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
    MEDIA_DOWNLOAD_EXPIRES: int = Field(default=3600)
    # 短期媒体/SSE 票据有效期（秒）。前端 <img>/<audio>/EventSource 无法带 header，
    # 改用此短票放进 ?token=，避免长效 access JWT 暴露在 URL/代理日志里。
    MEDIA_TOKEN_TTL: int = Field(default=300)

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

    # 按用户每分钟限流（放大成本/抓取的端点；详见 app/core/rate_limit.py）
    RATE_LIMIT_TASK_CREATE_PER_MIN: int = Field(default=20)
    RATE_LIMIT_UPLOAD_PRESIGN_PER_MIN: int = Field(default=30)
    RATE_LIMIT_SUMMARY_COMPARE_PER_MIN: int = Field(default=10)
    RATE_LIMIT_YOUTUBE_SYNC_PER_MIN: int = Field(default=10)

    # PromptHub
    PROMPTHUB_BASE_URL: str | None = Field(default=None)
    PROMPTHUB_API_KEY: str | None = Field(default=None)
    PROMPTHUB_CACHE_TTL: int = Field(default=300)  # seconds
    PROMPTHUB_IMAGE_GEN_PROJECT_ID: str | None = Field(default=None)

    @model_validator(mode="after")
    def _require_prod_secrets(self) -> Settings:
        """生产环境强制注入必需密钥，缺失即启动失败（fail-fast，不静默降级）。

        仅校验 production；dev/staging 不受影响。这些密钥须由 secrets manager /
        orchestrator 注入，绝不写入镜像或代码库。
        """
        if self.APP_ENV == "production":
            missing: list[str] = []
            if not self.FIELD_ENCRYPTION_KEY:
                missing.append("FIELD_ENCRYPTION_KEY")
            # 媒体/SSE 短票用 JWT_SECRET 自签（HS256）；生产缺失则无法签发安全的媒体 URL。
            if not self.JWT_SECRET:
                missing.append("JWT_SECRET")
            if missing:
                raise ValueError(
                    "生产环境缺少必需密钥（必须由 secrets manager/orchestrator 注入，不得写入镜像）: "
                    + ", ".join(missing)
                )
        return self


settings = Settings()
