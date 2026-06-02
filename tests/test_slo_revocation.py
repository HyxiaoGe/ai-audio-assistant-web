"""单点登出（SLO）：audio-web 在校验访问令牌后检查共享 Redis 的吊销标记。

访问令牌是无状态 RS256 JWT，签名校验离线完成 —— 用户在别处（如 fusion）退出登录后，
auth-service 已销毁会话并吊销刷新令牌，但 audio 手里这张访问令牌的签名仍然有效，会一直
被接受直到过期。auth-service 退出时向**共享 Redis** 写入 ``revoked_user:{sub}`` = 登出时刻，
audio-web 在 ``validator.verify_async`` 成功之后增加一次检查：``iat < 标记`` 即 401，使
「一处退出 = 处处退出」在下一次接口调用即生效（约定见 auth-service AUTH_CONTRACT.md）。

不依赖真实 Redis / auth-service：fake 校验器 + fake redis。
"""

from __future__ import annotations

import pytest
from auth.validator import AuthenticatedUser

from app.core import security
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode


class _FakeRedis:
    """最小异步 redis 替身：可预置标记值，或令 get 抛错以模拟 Redis 故障。"""

    def __init__(self, store: dict[str, str] | None = None, raise_exc: Exception | None = None) -> None:
        self._store = store or {}
        self._raise = raise_exc
        self.calls = 0

    async def get(self, key: str):  # noqa: ANN201 - 替身
        self.calls += 1
        if self._raise is not None:
            raise self._raise
        return self._store.get(key)


class _FakeValidator:
    def __init__(self, user: AuthenticatedUser) -> None:
        self._user = user

    async def verify_async(self, _token: str) -> AuthenticatedUser:
        return self._user


def _user(sub: str = "u1", iat: int = 1000) -> AuthenticatedUser:
    return AuthenticatedUser(sub=sub, email="a@b.c", raw_payload={"sub": sub, "iat": iat, "type": "access"})


def _wire(monkeypatch: pytest.MonkeyPatch, user: AuthenticatedUser, fake_redis: _FakeRedis) -> None:
    monkeypatch.setattr(security, "get_jwt_validator", lambda: _FakeValidator(user))
    monkeypatch.setattr(security, "get_redis_client", lambda: fake_redis)


@pytest.mark.asyncio
async def test_token_issued_before_logout_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _user(sub="u1", iat=1000)
    _wire(monkeypatch, user, _FakeRedis({"revoked_user:u1": "2000.0"}))
    with pytest.raises(BusinessError) as ei:
        await security.verify_access_token("tok")
    assert ei.value.code == ErrorCode.AUTH_TOKEN_INVALID


@pytest.mark.asyncio
async def test_token_issued_after_logout_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    # 退出后重新登录拿到的新令牌 iat > 登出时刻 —— 仍然有效。
    user = _user(sub="u1", iat=3000)
    _wire(monkeypatch, user, _FakeRedis({"revoked_user:u1": "2000.0"}))
    result = await security.verify_access_token("tok")
    assert result.sub == "u1"


@pytest.mark.asyncio
async def test_no_marker_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _user(sub="u2", iat=1000)
    _wire(monkeypatch, user, _FakeRedis({}))
    result = await security.verify_access_token("tok")
    assert result.sub == "u2"


@pytest.mark.asyncio
async def test_fractional_marker_revokes_same_second_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # 与 auth-service 一致的过度吊销语义：标记为小数墙钟秒、iat 为整数秒，严格 < 保证登出前
    # 所有令牌（含同秒内更早签发者）被吊销；重新登录因需多次往返，新令牌 iat 落入下一秒得以存活。
    fake = _FakeRedis({"revoked_user:u1": "2000.5"})
    _wire(monkeypatch, _user(sub="u1", iat=2000), fake)
    with pytest.raises(BusinessError):
        await security.verify_access_token("tok")

    _wire(monkeypatch, _user(sub="u1", iat=2001), fake)
    assert (await security.verify_access_token("tok")).sub == "u1"  # 下一秒（重新登录）存活


@pytest.mark.asyncio
async def test_redis_outage_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    # 该检查现处于每请求鉴权热路径：Redis 故障必须放行（视为未吊销）而非 500 拖垮全站，
    # 降级期吊销时延退化为令牌自身 <=15min 的 exp。断言确有触达 Redis 且吞掉了异常。
    user = _user(sub="u1", iat=1000)
    fake = _FakeRedis({}, raise_exc=ConnectionError("redis down"))
    _wire(monkeypatch, user, fake)
    result = await security.verify_access_token("tok")
    assert result.sub == "u1"  # 失败开放：未被拒
    assert fake.calls > 0  # 证明确实查询了 Redis 并吞掉了异常
