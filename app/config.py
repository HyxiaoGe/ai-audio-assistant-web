from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    APP_ENV: Literal["development", "staging", "production"] = Field(default="development")
    DEBUG: bool = Field(default=True)

    # API 外部访问地址（用于生成媒体文件 URL）
    API_BASE_URL: Optional[str] = Field(default="http://localhost:8000")

    DATABASE_URL: Optional[str] = Field(default=None)
    REDIS_URL: Optional[str] = Field(default=None)

    JWT_SECRET: Optional[str] = Field(default=None)
    JWT_ALGORITHM: Optional[str] = Field(default=None)

    CONFIG_CENTER_DB_ENABLED: bool = Field(default=True)
    CONFIG_CENTER_CACHE_TTL: int = Field(default=60)
    ADMIN_EMAILS: Optional[str] = Field(default=None)

    MINIO_ENDPOINT: Optional[str] = Field(default=None)
    MINIO_ACCESS_KEY: Optional[str] = Field(default=None)
    MINIO_SECRET_KEY: Optional[str] = Field(default=None)
    MINIO_BUCKET: Optional[str] = Field(default=None)
    MINIO_USE_SSL: Optional[bool] = Field(default=None)

    COS_REGION: Optional[str] = Field(default=None)
    COS_BUCKET: Optional[str] = Field(default=None)
    COS_SECRET_ID: Optional[str] = Field(default=None)
    COS_SECRET_KEY: Optional[str] = Field(default=None)
    COS_ENDPOINT: Optional[str] = Field(default=None)
    COS_USE_SSL: Optional[bool] = Field(default=True)
    COS_PUBLIC_READ: Optional[bool] = Field(default=False)

    OSS_ENDPOINT: Optional[str] = Field(default=None)
    OSS_REGION: Optional[str] = Field(default=None)
    OSS_BUCKET: Optional[str] = Field(default=None)
    OSS_USE_SSL: Optional[bool] = Field(default=True)

    TOS_ENDPOINT: Optional[str] = Field(default=None)
    TOS_REGION: Optional[str] = Field(default=None)
    TOS_BUCKET: Optional[str] = Field(default=None)
    TOS_ACCESS_KEY: Optional[str] = Field(default=None)
    TOS_SECRET_KEY: Optional[str] = Field(default=None)

    TENCENT_SECRET_ID: Optional[str] = Field(default=None)
    TENCENT_SECRET_KEY: Optional[str] = Field(default=None)
    TENCENT_REGION: Optional[str] = Field(default=None)
    TENCENT_ASR_APP_ID: Optional[str] = Field(default=None)
    TENCENT_ASR_ENGINE_MODEL_TYPE: Optional[str] = Field(default=None)
    TENCENT_ASR_ENGINE_MODEL_TYPE_FILE_FAST: Optional[str] = Field(default=None)
    TENCENT_ASR_CHANNEL_NUM: Optional[int] = Field(default=None)
    TENCENT_ASR_SOURCE_TYPE: Optional[int] = Field(default=None)
    TENCENT_ASR_RES_TEXT_FORMAT: Optional[int] = Field(default=None)
    TENCENT_ASR_SPEAKER_DIA: Optional[int] = Field(default=None)
    TENCENT_ASR_SPEAKER_NUMBER: Optional[int] = Field(default=None)
    TENCENT_ASR_POLL_INTERVAL: Optional[int] = Field(default=None)
    TENCENT_ASR_MAX_WAIT_SECONDS: Optional[int] = Field(default=None)
    ALIYUN_ACCESS_KEY_ID: Optional[str] = Field(default=None)
    ALIYUN_ACCESS_KEY_SECRET: Optional[str] = Field(default=None)
    ALIYUN_NLS_APP_KEY: Optional[str] = Field(default=None)
    VOLC_ASR_APP_ID: Optional[str] = Field(default=None)
    VOLC_ASR_ACCESS_TOKEN: Optional[str] = Field(default=None)
    VOLC_ASR_RESOURCE_ID: Optional[str] = Field(default=None)
    VOLC_ASR_MODEL_NAME: Optional[str] = Field(default=None)
    VOLC_ASR_MODEL_VERSION: Optional[str] = Field(default=None)
    VOLC_ASR_LANGUAGE: Optional[str] = Field(default=None)
    VOLC_ASR_ENABLE_ITN: Optional[bool] = Field(default=None)
    VOLC_ASR_SHOW_UTTERANCES: Optional[bool] = Field(default=None)
    VOLC_ASR_POLL_INTERVAL: Optional[int] = Field(default=None)
    VOLC_ASR_MAX_WAIT_SECONDS: Optional[int] = Field(default=None)

    DOUBAO_API_KEY: Optional[str] = Field(default=None)
    DOUBAO_BASE_URL: Optional[str] = Field(default=None)
    DOUBAO_MODEL: Optional[str] = Field(default=None)
    DOUBAO_MAX_TOKENS: Optional[int] = Field(default=None)
    QWEN_API_KEY: Optional[str] = Field(default=None)
    QWEN_MODEL: Optional[str] = Field(default=None)
    MOONSHOT_API_KEY: Optional[str] = Field(default=None)
    MOONSHOT_BASE_URL: Optional[str] = Field(default="https://api.moonshot.cn/v1")
    MOONSHOT_MODEL: Optional[str] = Field(default="moonshot-v1-8k")
    MOONSHOT_MAX_TOKENS: Optional[int] = Field(default=4096)
    DEEPSEEK_API_KEY: Optional[str] = Field(default=None)
    DEEPSEEK_BASE_URL: Optional[str] = Field(default="https://api.deepseek.com")
    DEEPSEEK_MODEL: Optional[str] = Field(default="deepseek-chat")
    DEEPSEEK_MAX_TOKENS: Optional[int] = Field(default=4096)
    OPENROUTER_API_KEY: Optional[str] = Field(default=None)
    OPENROUTER_BASE_URL: Optional[str] = Field(default="https://openrouter.ai/api/v1")
    OPENROUTER_MODEL: Optional[str] = Field(default=None)
    OPENROUTER_MAX_TOKENS: Optional[int] = Field(default=4096)
    OPENROUTER_HTTP_REFERER: Optional[str] = Field(default=None)
    OPENROUTER_APP_TITLE: Optional[str] = Field(default=None)
    OPENROUTER_DYNAMIC_MODELS: bool = Field(default=False)

    UPLOAD_ALLOWED_EXTENSIONS: Optional[str] = Field(default=None)
    UPLOAD_MAX_SIZE_BYTES: Optional[int] = Field(default=None)
    UPLOAD_PRESIGN_EXPIRES: Optional[int] = Field(default=None)

    YOUTUBE_DOWNLOAD_DIR: Optional[str] = Field(default=None)
    YOUTUBE_OUTPUT_TEMPLATE: Optional[str] = Field(default=None)
    YOUTUBE_DOWNLOAD_FORMAT: Optional[str] = Field(default=None)


settings = Settings()
