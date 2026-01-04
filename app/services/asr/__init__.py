from __future__ import annotations

from app.services.asr.aliyun import AliyunASRService
from app.services.asr.base import ASRService
from app.services.asr.tencent import TencentASRService

__all__ = ["ASRService", "TencentASRService", "AliyunASRService"]
