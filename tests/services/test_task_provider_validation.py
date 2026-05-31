"""任务创建期 provider/variant 校验。

未知 asr_variant 必须在创建阶段挡掉：否则会漏到 worker，consume_quota 调
get_pricing_config 返回 None 早抛 ValueError，免费额度周期分拆漏写（管理端成本台账少记）。
"""

from __future__ import annotations

import pytest

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.schemas.task import TaskCreateRequest, TaskOptions
from app.services.task_service import TaskService


def _req(**opts: object) -> TaskCreateRequest:
    return TaskCreateRequest(source_type="upload", options=TaskOptions(**opts))


def test_validate_rejects_unknown_asr_variant() -> None:
    with pytest.raises(BusinessError) as ei:
        TaskService._validate_provider_selection(_req(asr_variant="turbo"))
    assert ei.value.code == ErrorCode.PARAMETER_ERROR


@pytest.mark.parametrize("variant", ["file", "file_fast", None])
def test_validate_accepts_known_asr_variant(variant: str | None) -> None:
    # 已知变体（及未指定）必须放行，不抛异常。
    TaskService._validate_provider_selection(_req(asr_variant=variant))
