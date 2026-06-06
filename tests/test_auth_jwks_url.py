"""JWKS 取用 URL 的解析优先级测试。

根因：JWKS 经公网 cloudflared 隧道拉取 ~1.5s/次，JWTValidator 缓存仅 300s，一过期、并发认证
请求各拉一遍（单 worker、库内无 singleflight）即造成「开页齐卡」尾延迟。修复：JWKS 与
userinfo/profile 同属「后端↔auth-service」内部调用，优先走 LAN 内部基址（~13ms，keys 一致）。

优先级：AUTH_SERVICE_INTERNAL_URL > AUTH_SERVICE_JWKS_URL > AUTH_SERVICE_URL。
"""

from __future__ import annotations

from app.config import settings


def _with(**overrides):
    # model_copy(update=) 直接覆盖字段、不重跑校验，适合单测纯属性逻辑。
    return settings.model_copy(update=overrides)


def test_jwks_prefers_internal_lan_over_explicit_public():
    s = _with(
        AUTH_SERVICE_INTERNAL_URL="http://192.168.1.11:8100",
        AUTH_SERVICE_JWKS_URL="https://auth.example.com/.well-known/jwks.json",
        AUTH_SERVICE_URL="https://auth.example.com",
    )
    assert s.resolved_auth_jwks_url == "http://192.168.1.11:8100/.well-known/jwks.json"


def test_jwks_strips_trailing_slash_on_internal_base():
    s = _with(
        AUTH_SERVICE_INTERNAL_URL="http://192.168.1.11:8100/",
        AUTH_SERVICE_JWKS_URL=None,
    )
    assert s.resolved_auth_jwks_url == "http://192.168.1.11:8100/.well-known/jwks.json"


def test_jwks_falls_back_to_explicit_url_when_no_internal():
    s = _with(
        AUTH_SERVICE_INTERNAL_URL=None,
        AUTH_SERVICE_JWKS_URL="https://auth.example.com/custom/jwks",
        AUTH_SERVICE_URL="https://auth.example.com",
    )
    assert s.resolved_auth_jwks_url == "https://auth.example.com/custom/jwks"


def test_jwks_derives_from_auth_url_when_nothing_set():
    s = _with(
        AUTH_SERVICE_INTERNAL_URL=None,
        AUTH_SERVICE_JWKS_URL=None,
        AUTH_SERVICE_URL="https://auth.example.com",
    )
    assert s.resolved_auth_jwks_url == "https://auth.example.com/.well-known/jwks.json"
