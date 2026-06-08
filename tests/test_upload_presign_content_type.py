"""回归：上传预签名必须把 content_type 绑进 OSS 签名。

OSS 签名 V1 把 Content-Type 计入 string-to-sign；浏览器 XHR PUT 会带
Content-Type=file.type（与 presign 请求里的 content_type 同源）。若后端签名时不绑 content_type，
OSS 因两边 Content-Type 不一致返回 SignatureDoesNotMatch(403)，所有浏览器直传都会失败。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.api.v1 import upload as upload_module
from app.services.storage.oss import OSSStorageService


@pytest.mark.asyncio
async def test_build_upload_url_threads_content_type_to_presign(monkeypatch: pytest.MonkeyPatch) -> None:
    """presign 端点的 content_type 必须一路传到 storage.presign_put_object。"""
    recorded: dict[str, Any] = {}

    class _FakeStorage:
        def presign_put_object(self, object_name: str, expires_in: int, content_type: str | None = None) -> str:
            recorded["object_name"] = object_name
            recorded["expires_in"] = expires_in
            recorded["content_type"] = content_type
            return f"https://oss.example/{object_name}?sig=fake"

    async def fake_get_service(service_type: str, **kwargs: Any) -> _FakeStorage:  # noqa: ARG001
        return _FakeStorage()

    monkeypatch.setattr(upload_module.SmartFactory, "get_service", fake_get_service)

    url = await upload_module._build_upload_url("upload/u/2026/01/01/abc.mp3", 600, "u", "audio/mpeg")

    assert recorded["content_type"] == "audio/mpeg"
    assert recorded["object_name"] == "upload/u/2026/01/01/abc.mp3"
    assert recorded["expires_in"] == 600
    assert url.startswith("https://oss.example/")


def test_oss_presign_put_binds_content_type_into_signature() -> None:
    """OSSStorageService.presign_put_object 给定 content_type 时必须把它作为 Content-Type
    header 传入 sign_url；不给时保持 headers=None（向后兼容）。"""

    class _FakeBucket:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def sign_url(
            self,
            method: str,
            key: str,
            expires: int,
            slash_safe: bool = False,  # noqa: ARG002
            headers: dict[str, str] | None = None,
            **_kwargs: Any,
        ) -> str:
            self.calls.append({"method": method, "key": key, "headers": headers})
            return "https://signed.example"

    # 绕开 __init__（需要真实 OSS 配置），只注入 _bucket
    svc = object.__new__(OSSStorageService)
    svc._bucket = _FakeBucket()  # type: ignore[attr-defined]

    svc.presign_put_object("upload/x.mp3", 600, "audio/mpeg")
    assert svc._bucket.calls[0]["method"] == "PUT"  # type: ignore[attr-defined]
    assert svc._bucket.calls[0]["headers"] == {"Content-Type": "audio/mpeg"}  # type: ignore[attr-defined]

    svc.presign_put_object("upload/y.mp3", 600)
    assert svc._bucket.calls[1]["headers"] is None  # type: ignore[attr-defined]
