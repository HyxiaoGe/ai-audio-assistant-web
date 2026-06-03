"""通知类型表 / 枚举 / 模板的完备性与一致性单测。"""

from __future__ import annotations


def test_notifications_package_importable() -> None:
    # 包必须可导入，作为后续 types/service/channels 的命名空间根
    import app.services.notifications  # noqa: F401
