"""YouTube HttpError 分类器：把 API 错误分成「配额耗尽(不可重试)」「瞬态限流/5xx(应退避重试)」
「其它(照旧处理)」三类，供同步任务做配额感知的软着陆决策。

背景：同步任务原先把所有 HttpError 一律就地咽掉——配额耗尽被伪装成「同步 0 条」并推进调度，
而真正瞬态、本应退避重试的 rateLimitExceeded/5xx 也从不重试。分类器让两者分流。
"""

from __future__ import annotations

import json

import pytest

from app.services.youtube.api_errors import (
    OTHER,
    QUOTA,
    RATE_LIMIT,
    classify_youtube_http_error,
)


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "reason"


def _http_error(status: int, reason: str):
    from googleapiclient.errors import HttpError

    content = json.dumps(
        {"error": {"code": status, "message": "msg", "errors": [{"reason": reason}]}}
    ).encode("utf-8")
    return HttpError(_Resp(status), content)


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        (403, "quotaExceeded", QUOTA),
        (403, "dailyLimitExceeded", QUOTA),
        (403, "rateLimitExceeded", RATE_LIMIT),
        (403, "userRateLimitExceeded", RATE_LIMIT),
        (429, "tooManyRequests", RATE_LIMIT),
        (500, "backendError", RATE_LIMIT),
        (503, "backendError", RATE_LIMIT),
        (403, "forbidden", OTHER),
        (404, "notFound", OTHER),
        (401, "authError", OTHER),
    ],
)
def test_classify_youtube_http_error(status: int, reason: str, expected: str) -> None:
    assert classify_youtube_http_error(_http_error(status, reason)) == expected
