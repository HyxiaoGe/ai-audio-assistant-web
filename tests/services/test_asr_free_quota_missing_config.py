"""缺少 ASR 定价配置时 consume_quota 必须发出可检索的 WARNING（COV-cfg）。

provider/variant 已注册但未在 asr_pricing_configs 播种时，consume_quota 在分拆免费/付费
之前就早抛 ValueError，跳过 AsrUsagePeriod 台账写入（管理端少记；用户经 ASRUsage 兜底仍正确
计费）。此时必须 WARN 出 provider/variant，便于 ops 定位并播种定价，而不是静默漏记。

注：审计提议的「缺配置时防御性补写 AsrUsagePeriod」经对抗复核判定为 WON'T-FIX——既不安全
（无 reset_period 只能猜周期桶，与日后播种的真实桶冲突成孤儿重复行）也无效（管理端总览按
config 迭代，永不读取无 config 的周期行）。故此处只做可观测性告警。
"""

from __future__ import annotations

import logging

import pytest

from app.services import asr_free_quota_service
from app.services.asr_free_quota_service import AsrFreeQuotaService


async def test_consume_quota_warns_when_pricing_config_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def _no_config(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(asr_free_quota_service, "get_pricing_config", _no_config)

    with caplog.at_level(logging.WARNING, logger="app.services.asr_free_quota_service"):
        with pytest.raises(ValueError):
            await AsrFreeQuotaService.consume_quota(object(), "ghostprovider", "file", 120.0)

    # 告警须命名 provider/variant，便于 grep 定位
    assert "ghostprovider" in caplog.text
    assert "file" in caplog.text
    assert any(r.levelno == logging.WARNING for r in caplog.records)
