"""通知渠道子包：可插拔、注册式、逐渠道错误隔离。

import 本子包即触发各渠道的 @register_channel 副作用（复刻 app/services/asr/__init__.py
的注册式 import 约定）。消费方只要 import 本子包或其任一子模块（如 .base），父包
__init__ 先行执行即完成注册，无需在别处手动 import 各渠道模块。
"""

from __future__ import annotations

from app.services.notifications.channels import feishu as _feishu  # noqa: F401
from app.services.notifications.channels import in_app as _in_app  # noqa: F401
