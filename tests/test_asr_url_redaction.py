"""redact_audio_url 纯函数单测。

预签名下载 URL 把签名/凭证/有效期放在 query；写日志前必须剥离 query 与 userinfo，
避免把有时效的可下载链接泄露给任何能看日志的人。
"""

from __future__ import annotations

from app.services.asr.base import redact_audio_url


def test_cos_presigned_query_stripped() -> None:
    src = (
        "https://bucket.cos.ap.myqcloud.com/upload/u1/abc.mp3"
        "?q-sign-algorithm=sha1&q-signature=DEADBEEF&q-key-time=x"
    )
    result = redact_audio_url(src)
    assert result == "https://bucket.cos.ap.myqcloud.com/upload/u1/abc.mp3"
    assert "q-signature" not in result
    assert "DEADBEEF" not in result


def test_aws_minio_signature_stripped() -> None:
    result = redact_audio_url("https://h/b/k?X-Amz-Signature=abc&X-Amz-Credential=AKIA")
    assert "X-Amz-Signature" not in result
    assert "X-Amz-Credential" not in result


def test_userinfo_stripped() -> None:
    result = redact_audio_url("https://user:pass@host/path?sig=1")
    assert result == "https://host/path"
    assert "pass" not in result


def test_port_preserved() -> None:
    assert redact_audio_url("http://host:9000/bucket/key?sig=1") == "http://host:9000/bucket/key"


def test_ipv6_host_and_port_preserved() -> None:
    # parts.hostname would corrupt this by dropping the [] brackets; rsplit('@') preserves them.
    assert redact_audio_url("http://[2001:db8::1]:9000/k?sig=1") == "http://[2001:db8::1]:9000/k"


def test_fragment_dropped() -> None:
    assert redact_audio_url("https://h/p#frag") == "https://h/p"


def test_opaque_and_local_passthrough() -> None:
    assert redact_audio_url("upload/u1/2026/05/30/abc.mp3") == "upload/u1/2026/05/30/abc.mp3"
    assert redact_audio_url("/tmp/x.mp3") == "/tmp/x.mp3"


def test_malformed_degrades_safely() -> None:
    # No leak even if urlsplit raises on a malformed IPv6 literal.
    assert redact_audio_url("http://[::1/path?sig=1") == "<redacted-url>"
