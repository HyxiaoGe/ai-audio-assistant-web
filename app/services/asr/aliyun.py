from __future__ import annotations

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.services.asr.base import ASRService, TranscriptSegment


class AliyunASRService(ASRService):
    def __init__(self) -> None:
        access_key_id = settings.ALIYUN_ACCESS_KEY_ID
        access_key_secret = settings.ALIYUN_ACCESS_KEY_SECRET
        if not access_key_id or not access_key_secret:
            raise RuntimeError("ALIYUN_ACCESS_KEY_ID or ALIYUN_ACCESS_KEY_SECRET is not set")

    async def transcribe(
        self, audio_url: str, status_callback=None
    ) -> list[TranscriptSegment]:
        if not audio_url:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="audio_url")
        raise BusinessError(ErrorCode.ASR_SERVICE_UNAVAILABLE)
