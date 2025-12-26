from __future__ import annotations

from app.config import settings
from app.services.asr.aliyun import AliyunASRService
from app.services.asr.base import ASRService
from app.services.asr.tencent import TencentASRService


def get_asr_service() -> ASRService:
    provider = settings.ASR_PROVIDER
    if provider == "tencent":
        return TencentASRService()
    if provider == "aliyun":
        return AliyunASRService()
    raise RuntimeError("ASR_PROVIDER is not set or unsupported")
