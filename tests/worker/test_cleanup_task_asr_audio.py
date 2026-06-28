"""回归：任务清理需连带删除送 ASR 的转码副本 asr16k.mp3，否则 OSS 永久残留孤儿对象。

process_audio 在转写前为 upload 任务生成独立落库的 `{source}.asr16k.mp3`。cleanup_task_data
此前只删 task.source_key，会漏掉转码副本。这里验证 upload 任务清理时派生键也被删、且非 upload
任务不会多删。
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import worker.tasks.cleanup_task as ct


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


_UPLOAD_KEY = "upload/user-1/2026/05/30/" + "a" * 32 + ".mp4"
_ASR_KEY = "upload/user-1/2026/05/30/" + "a" * 32 + ".asr16k.mp3"


def test_cleanup_deletes_derived_asr_audio_for_upload(monkeypatch) -> None:
    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(ct, "_delete_storage_object", lambda provider, key, user_id: deleted.append((provider, key)))
    monkeypatch.setattr(ct, "_storage_cleanup_providers", lambda: ["oss", "cos", "minio"])
    monkeypatch.setattr(
        ct,
        "_load_task",
        lambda session, task_id, user_id: SimpleNamespace(
            source_key=_UPLOAD_KEY, source_type="upload", deleted_at=object()
        ),
    )
    _patch_session(monkeypatch)

    ct.cleanup_task_data("task-1", "user-1")

    deleted_keys = {key for _, key in deleted}
    assert _UPLOAD_KEY in deleted_keys
    assert _ASR_KEY in deleted_keys
    # 派生键在主存储 OSS 上被删（过渡期 cos/minio 也 best-effort 删）
    assert ("oss", _ASR_KEY) in deleted


def test_cleanup_skips_derived_key_for_non_upload(monkeypatch) -> None:
    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(ct, "_delete_storage_object", lambda provider, key, user_id: deleted.append((provider, key)))
    monkeypatch.setattr(ct, "_storage_cleanup_providers", lambda: ["oss", "cos", "minio"])
    # youtube 任务的 source_key 是转码后的 wav，没有 asr16k.mp3 派生副本
    yt_key = "youtube/user-1/2026/05/30/" + "b" * 32 + ".wav"
    monkeypatch.setattr(
        ct,
        "_load_task",
        lambda session, task_id, user_id: SimpleNamespace(
            source_key=yt_key, source_type="youtube", deleted_at=object()
        ),
    )
    _patch_session(monkeypatch)

    ct.cleanup_task_data("task-2", "user-1")

    assert all(".asr16k.mp3" not in key for _, key in deleted)
    assert yt_key in {key for _, key in deleted}
