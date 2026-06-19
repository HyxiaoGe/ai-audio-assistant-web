"""溯源 PR4:ASR 服务暴露「指定变体下实际使用的引擎/模型」用于 Task 级溯源。

ASR provider 的具体引擎(如 tencent 的 16k_zh / 极速版方言引擎)随 variant 变化,且
此前只埋在 provider 内部(_create_task / flash 各自就地解析),Task 上无从记录。本组测试
钉住 `ASRService.engine_for_variant(variant)` 契约:基类默认 None(无引擎概念的 provider
不报错、留 NULL),tencent 据 variant 返回实际引擎。不起网络/不动全局 settings(传 config dict)。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.services.asr.base import ASRService, TranscriptSegment
from app.services.asr.tencent import TencentASRService

# 完整 config dict:get_config_value 命中 dict 即不回落 settings,构造不触网/不读全局密钥。
_CFG = {
    "secret_id": "sid",
    "secret_key": "skey",
    "region": "ap-shanghai",
    "engine_model_type": "16k_zh",
    "engine_model_type_file_fast": "16k_zh_dialect",
    "channel_num": 1,
    "res_text_format": 0,
    "speaker_dia": 1,
    "speaker_number": 2,
    "poll_interval": 1,
    "max_wait": 5,
    "source_type": 0,
}


class _BareASR(ASRService):
    """最小具体子类:不覆写 engine_for_variant,验证基类默认行为。"""

    @property
    def provider(self) -> str:
        return "bare"

    async def transcribe(
        self,
        audio_url: str,
        status_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[TranscriptSegment]:
        return []

    async def get_task_status(self, task_id: str) -> str:
        return "completed"

    async def cancel_task(self, task_id: str) -> bool:
        return False

    async def batch_transcribe(
        self,
        audio_urls: list[str],
        status_callback: Callable[[str, int, int], Awaitable[None]] | None = None,
    ) -> list[list[TranscriptSegment]]:
        return []

    async def health_check(self) -> bool:
        return True

    def estimate_cost(self, duration_seconds: int, variant: str = "file") -> float:
        return 0.0


def test_base_engine_for_variant_defaults_to_none() -> None:
    # 无引擎概念的 provider 不该崩,返回 None(Task 级溯源留 NULL,前端不显示徽章)。
    assert _BareASR().engine_for_variant("file") is None
    assert _BareASR().engine_for_variant(None) is None


def test_tencent_engine_for_variant_file_returns_default_engine() -> None:
    svc = TencentASRService(_CFG)
    assert svc.engine_for_variant("file") == "16k_zh"


def test_tencent_engine_for_variant_none_returns_default_engine() -> None:
    svc = TencentASRService(_CFG)
    assert svc.engine_for_variant(None) == "16k_zh"


def test_tencent_engine_for_variant_file_fast_returns_fast_engine() -> None:
    svc = TencentASRService(_CFG)
    assert svc.engine_for_variant("file_fast") == "16k_zh_dialect"


def test_tencent_engine_for_variant_file_fast_falls_back_when_unset() -> None:
    svc = TencentASRService(_CFG)
    svc._engine_model_type_file_fast = None  # 未配置极速版引擎 → 回落标准引擎
    assert svc.engine_for_variant("file_fast") == "16k_zh"
