from __future__ import annotations

from app.services.feature.flags import is_discover_enabled


class _FakeResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _FakeSession:
    """最小 async 会话替身:execute 返回预置的 enabled 列标量。

    直接对着 kill-switch 真源(service_configs.enabled 列)测,而非在 ConfigManager
    实例缓存层播种——后者绕过了「列 → gate」这道缝,正是旧实现漏掉 bug 的原因。
    """

    def __init__(self, value: object) -> None:
        self._value = value

    async def execute(self, _stmt: object) -> _FakeResult:
        return _FakeResult(self._value)


async def test_default_enabled_when_no_row() -> None:
    # 无配置行 → 默认开(fail-safe default-on)
    assert await is_discover_enabled(_FakeSession(None)) is True


async def test_disabled_when_column_false() -> None:
    # 行 enabled 列为 False → gate 拦截(这正是旧实现读不到、恒放行的场景)
    assert await is_discover_enabled(_FakeSession(False)) is False


async def test_enabled_when_column_true() -> None:
    assert await is_discover_enabled(_FakeSession(True)) is True


async def test_fail_open_on_read_error() -> None:
    class _BoomSession:
        async def execute(self, _stmt: object) -> object:
            raise RuntimeError("db down")

    assert await is_discover_enabled(_BoomSession()) is True
