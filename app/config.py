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

    APP_ENV: Literal["development", "staging", "production"] = Field(
        default="development"
    )
    DEBUG: bool = Field(default=True)

    DATABASE_URL: Optional[str] = Field(default=None)
    REDIS_URL: Optional[str] = Field(default=None)

    JWT_SECRET: Optional[str] = Field(default=None)
    JWT_ALGORITHM: Optional[str] = Field(default=None)

    STORAGE_PROVIDER: Optional[Literal["minio", "cos"]] = Field(default="minio")

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

    ASR_PROVIDER: Optional[Literal["tencent", "aliyun"]] = Field(default=None)
    TENCENT_SECRET_ID: Optional[str] = Field(default=None)
    TENCENT_SECRET_KEY: Optional[str] = Field(default=None)
    TENCENT_REGION: Optional[str] = Field(default=None)
    TENCENT_ASR_ENGINE_MODEL_TYPE: Optional[str] = Field(default=None)
    TENCENT_ASR_CHANNEL_NUM: Optional[int] = Field(default=None)
    TENCENT_ASR_SOURCE_TYPE: Optional[int] = Field(default=None)
    TENCENT_ASR_RES_TEXT_FORMAT: Optional[int] = Field(default=None)
    TENCENT_ASR_SPEAKER_DIA: Optional[int] = Field(default=None)
    TENCENT_ASR_SPEAKER_NUMBER: Optional[int] = Field(default=None)
    TENCENT_ASR_POLL_INTERVAL: Optional[int] = Field(default=None)
    TENCENT_ASR_MAX_WAIT_SECONDS: Optional[int] = Field(default=None)
    ALIYUN_ACCESS_KEY_ID: Optional[str] = Field(default=None)
    ALIYUN_ACCESS_KEY_SECRET: Optional[str] = Field(default=None)

    LLM_PROVIDER: Optional[Literal["doubao", "qwen"]] = Field(default=None)
    DOUBAO_API_KEY: Optional[str] = Field(default=None)
    DOUBAO_BASE_URL: Optional[str] = Field(default=None)
    DOUBAO_MODEL: Optional[str] = Field(default=None)
    DOUBAO_MAX_TOKENS: Optional[int] = Field(default=None)
    QWEN_API_KEY: Optional[str] = Field(default=None)
    QWEN_MODEL: Optional[str] = Field(default=None)

    UPLOAD_ALLOWED_EXTENSIONS: Optional[str] = Field(default=None)
    UPLOAD_MAX_SIZE_BYTES: Optional[int] = Field(default=None)
    UPLOAD_PRESIGN_EXPIRES: Optional[int] = Field(default=None)

    YOUTUBE_DOWNLOAD_DIR: Optional[str] = Field(default=None)
    YOUTUBE_OUTPUT_TEMPLATE: Optional[str] = Field(default=None)
    YOUTUBE_DOWNLOAD_FORMAT: Optional[str] = Field(default=None)


settings = Settings()
