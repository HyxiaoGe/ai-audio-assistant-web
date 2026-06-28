"""回归：任务清理只能遍历**已配置**的存储后端。

历史上 cleanup_task 盲目对 oss/cos/minio 逐个实例化删除。本部署只配 OSS，未配置的
cos/minio 实例化必失败，底层 config_manager/registry 打出 ERROR 日志，被运维哨兵当作
故障转发飞书告警（实为良性）。修复后清理只遍历 ``_storage_cleanup_providers()`` 返回的
已配置后端，从根上不再触碰未配置后端。
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import worker.tasks.cleanup_task as ct
from app.core.config_manager import ConfigManager


class _FakeSession:
    def execute(self, *args: object, **kwargs: object) -> None:
        return None

    def commit(self) -> None:
        return None


def _patch_session(monkeypatch) -> None:
    @contextmanager
    def _factory():
        yield _FakeSession()

    monkeypatch.setattr(ct, "get_sync_db_session", _factory)


_KEY = "youtube/user-1/2026/05/30/" + "c" * 32 + ".wav"


def test_cleanup_only_touches_configured_providers(monkeypatch) -> None:
    """只有 OSS 配置时，cleanup 不应对 cos/minio 发起任何删除（即不会去实例化它们）。"""
    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(ct, "_delete_storage_object", lambda provider, key, user_id: deleted.append((provider, key)))
    # 真实走 _storage_cleanup_providers -> ConfigManager.is_configured：只放行 oss
    monkeypatch.setattr(
        ConfigManager,
        "is_configured",
        classmethod(lambda cls, service_type, name: service_type == "storage" and name == "oss"),
    )
    monkeypatch.setattr(
        ct,
        "_load_task",
        lambda session, task_id, user_id: SimpleNamespace(source_key=_KEY, source_type="youtube", deleted_at=object()),
    )
    _patch_session(monkeypatch)

    ct.cleanup_task_data("task-x", "user-1")

    touched_providers = {provider for provider, _ in deleted}
    assert touched_providers == {"oss"}
    assert ("cos", _KEY) not in deleted
    assert ("minio", _KEY) not in deleted


def test_cleanup_skips_object_deletion_when_no_provider_configured(monkeypatch) -> None:
    """无任何存储后端配置时，cleanup 不删任何对象但仍继续清理 DB（不抛异常）。"""
    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(ct, "_delete_storage_object", lambda provider, key, user_id: deleted.append((provider, key)))
    monkeypatch.setattr(ct, "_storage_cleanup_providers", lambda: [])
    monkeypatch.setattr(
        ct,
        "_load_task",
        lambda session, task_id, user_id: SimpleNamespace(source_key=_KEY, source_type="youtube", deleted_at=object()),
    )
    _patch_session(monkeypatch)

    ct.cleanup_task_data("task-y", "user-1")

    assert deleted == []
