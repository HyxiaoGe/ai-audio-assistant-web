"""get_public_viewer:公开探索端点的「可选观看者」依赖。

契约:无 token → 匿名 None;合法登录态 → 解析出 CurrentUser;token 无效/过期 → 仍回落 None
(绝不让坏 token 打断匿名浏览,与 get_current_user_optional 的「坏 token propagate」不同)。
"""

from __future__ import annotations

from app.api import deps
from app.api.deps import CurrentUser, get_public_viewer
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode


async def test_no_authorization_returns_none() -> None:
    assert await get_public_viewer(db=object(), authorization=None) is None


async def test_valid_token_returns_user(monkeypatch) -> None:
    async def _fake(db, authorization):  # noqa: ANN001, ANN202
        return CurrentUser(id="u1", email="a@b")

    monkeypatch.setattr(deps, "get_current_user", _fake)
    user = await get_public_viewer(db=object(), authorization="Bearer good")
    assert user is not None and user.id == "u1"


async def test_invalid_token_returns_none(monkeypatch) -> None:
    async def _fake(db, authorization):  # noqa: ANN001, ANN202
        raise BusinessError(ErrorCode.AUTH_TOKEN_INVALID)

    monkeypatch.setattr(deps, "get_current_user", _fake)
    assert await get_public_viewer(db=object(), authorization="Bearer bad") is None
