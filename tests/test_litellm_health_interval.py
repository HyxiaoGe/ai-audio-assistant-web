"""litellm_health 探测间隔解析测试。

/health 会对 DB 里每个模型各打一次真实 completion，其中 qwen reasoning 模型
每次生成数百 reasoning token（enable_thinking/max_tokens 都压不掉），频繁探测
在服务商侧产生真实费用。默认间隔应放宽到 30min，并支持 env 即时覆盖。
详见 https://github.com/HyxiaoGe/ai-audio-assistant-web/issues/68
"""

from __future__ import annotations

from app.core import litellm_health


def test_default_refresh_interval_is_30min(monkeypatch):
    """未设 env 时默认 1800s（30min），而不是过去的 300s。"""
    monkeypatch.delenv("LITELLM_HEALTH_INTERVAL_SECONDS", raising=False)
    assert litellm_health._resolve_refresh_interval() == 1800.0


def test_refresh_interval_env_override(monkeypatch):
    """运维可用 env 即时覆盖（改 env + 重启即可，无需改代码）。"""
    monkeypatch.setenv("LITELLM_HEALTH_INTERVAL_SECONDS", "600")
    assert litellm_health._resolve_refresh_interval() == 600.0
