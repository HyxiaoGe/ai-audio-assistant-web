"""读取 LiteLLM 的 end-user spend($),供管理员成本看板的「LLM」列。

LLM 全量经 LiteLLM 代理,LiteLLM 内置定价 + SpendLogs 是 LLM 成本的权威账本。PR-1 已给每条
请求体打 user=app_user_id 标签,LiteLLM 据此按 end-user/customer 累计 spend;这里用代理 master
key 调 GET /customer/info?end_user_id=<id> 取回(美元)。

master key 缺失时 available=False、直接返回空 —— 让看板的 ¥(ASR/配图)两列照常工作,LLM 列
显示「未配置」,绝不因 LLM 来源不可用而整表失败。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class LiteLLMSpendClient:
    """按 end-user 拉 LiteLLM spend。master key 由 secret manager 注入,绝不进日志。"""

    def __init__(
        self,
        base_url: str | None = None,
        master_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = (base_url or settings.LITELLM_BASE_URL).rstrip("/")
        # master_key 显式传入(含 "")优先;未传(None)才回落配置。
        self._master_key = master_key if master_key is not None else settings.LITELLM_MASTER_KEY
        self._transport = transport
        self._timeout = timeout

    @property
    def available(self) -> bool:
        """有 master key 才能读 end-user spend;否则看板 LLM 列降级为「未配置」。"""
        return bool(self._master_key)

    async def spend_by_end_user(self, user_ids: Iterable[str]) -> dict[str, float]:
        """{user_id: spend($)}。不可用直接返回 {} 不发请求;单个用户失败跳过不连累整体。"""
        if not self.available:
            return {}

        result: dict[str, float] = {}
        headers = {"Authorization": f"Bearer {self._master_key}"}
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            for uid in user_ids:
                try:
                    resp = await client.get("/customer/info", params={"end_user_id": uid})
                except httpx.HTTPError as exc:
                    logger.warning("LiteLLM /customer/info 请求失败 user=%s: %s", uid, exc)
                    continue
                if resp.status_code != 200:
                    # 400/404 常见于「该 end-user 在 LiteLLM 尚无记录」—— 跳过,非错误。
                    continue
                try:
                    spend = float(resp.json().get("spend") or 0.0)
                except (ValueError, TypeError, KeyError) as exc:
                    logger.warning("LiteLLM /customer/info 响应解析失败 user=%s: %s", uid, exc)
                    continue
                result[uid] = spend
        return result
