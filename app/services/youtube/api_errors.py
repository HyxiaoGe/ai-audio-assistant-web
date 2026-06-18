"""Classify YouTube Data API HttpError for quota-aware sync handling.

YouTube 同步任务原先把所有 HttpError 一律就地咽掉，导致两类相反的错误处理都失当：
  - 配额耗尽(quotaExceeded/dailyLimitExceeded)被伪装成「同步 0 条 + 推进调度」，污染发布
    频率统计、拖慢自适应调度；且撞墙当天每个频道刷一条 ERROR+traceback，易误触错误尖峰告警。
  - 真正瞬态、本应退避重试的 rateLimitExceeded / 5xx 也被一并咽掉，从不重试。

本分类器把错误分成三类，供任务分流：
  - QUOTA      : 配额耗尽，不可重试（重试只会继续锤已枯竭的项目级配额）。软着陆：不推进调度、
                 返回 quota_exceeded、warning 记录（无 traceback），等次日太平洋时间 0 点重置或下个窗口。
  - RATE_LIMIT : 瞬态限流 / 服务端 5xx，应退避重试（交给 Celery autoretry 的指数退避）。
  - OTHER      : 其它(403 forbidden / 404 / 401 等)，按各调用点既有逻辑处理。

注意：配额是 GCP 项目级、全平台共享(非每用户)，故配额耗尽对任何重试都无意义。
"""

from __future__ import annotations

QUOTA = "quota"
RATE_LIMIT = "rate_limit"
OTHER = "other"

_QUOTA_REASONS = ("quotaexceeded", "dailylimitexceeded")
_RATE_LIMIT_REASONS = ("ratelimitexceeded", "userratelimitexceeded")


def classify_youtube_http_error(error: object) -> str:
    """Classify a googleapiclient HttpError into QUOTA / RATE_LIMIT / OTHER.

    Duck-typed on `error.resp.status` (int) + `error.content` (bytes/str) so it works with
    real HttpError without importing it here. Reason tokens are matched case-insensitively
    against both the raw content and the string form (HttpError str() formatting varies).
    """
    status = getattr(getattr(error, "resp", None), "status", None)

    content = getattr(error, "content", b"") or b""
    if isinstance(content, (bytes, bytearray)):
        content = content.decode("utf-8", "ignore")
    blob = f"{content} {error}".lower()

    if status == 403 and any(reason in blob for reason in _QUOTA_REASONS):
        return QUOTA
    if status == 429 or any(reason in blob for reason in _RATE_LIMIT_REASONS):
        return RATE_LIMIT
    if isinstance(status, int) and status >= 500:
        return RATE_LIMIT
    return OTHER
