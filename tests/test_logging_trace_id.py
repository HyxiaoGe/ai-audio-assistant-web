"""P3-1: 结构化日志 + trace_id 关联。

trace_id 基建已在(app.core.response 的 contextvar + RequestIDMiddleware),但:
- 无 Filter 把 trace_id 注入 LogRecord;
- main.py 的 general_exception_handler 记 500 时不带 trace_id,无法与返回给客户端的 traceId 关联;
- worker 任务把 request_id 当参数传,却从不写进 contextvar,worker 日志全无 trace_id。
本测试钉:Filter 注入 + configure_logging 幂等 + 500 日志与响应共享 trace_id + worker ctx 包裹。
"""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from app.core.logging_config import TraceIdFilter, configure_logging, task_trace_context
from app.core.response import get_request_id, set_request_id
from app.main import create_app


def _make_record() -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)


def test_trace_id_filter_injects_ctx() -> None:
    token = set_request_id("abc123")
    try:
        record = _make_record()
        assert TraceIdFilter().filter(record) is True
        assert record.trace_id == "abc123"
    finally:
        from app.core.response import reset_request_id

        reset_request_id(token)


def test_trace_id_filter_fallback_uuid_when_no_ctx() -> None:
    record = _make_record()
    TraceIdFilter().filter(record)
    # ctx 为空 → get_request_id() 回落到新 uuid hex(非空),Formatter 引用 %(trace_id)s 不会 KeyError
    assert isinstance(record.trace_id, str) and record.trace_id


def test_configure_logging_is_idempotent() -> None:
    root = logging.getLogger()
    configure_logging()
    handlers_after_first = list(root.handlers)
    configure_logging()
    # 不叠加 handler(否则 uvicorn --reload / 每用例 create_app / celery prefork 回收会日志重复)
    assert root.handlers == handlers_after_first
    # 每个 handler 恰好一个 TraceIdFilter
    for handler in root.handlers:
        assert sum(isinstance(f, TraceIdFilter) for f in handler.filters) == 1


def test_configure_logging_never_raises_on_setup_failure(monkeypatch) -> None:
    # 评审 MEDIUM:日志配置在 app/worker 启动早期调用——若内部装配抛错,绝不能拖垮启动。
    import app.core.logging_config as logging_config

    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("formatter blew up")

    monkeypatch.setattr(logging_config.logging, "Formatter", _boom)
    configure_logging()  # 不抛错即通过(吞掉内部失败,退化为无 trace_id 但能启动)


def test_unhandled_exception_log_and_response_share_trace_id(caplog) -> None:
    app = create_app()

    @app.get("/boom-trace")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR):
        response = client.get("/boom-trace", headers={"X-Request-Id": "trace-xyz-1"})

    # 返回给客户端的 traceId == 我们注入的 X-Request-Id
    assert response.json()["traceId"] == "trace-xyz-1"
    # 且 500 的异常日志行带同一个 trace_id(可关联)
    assert any("trace-xyz-1" in rec.getMessage() for rec in caplog.records), (
        "异常日志未带 trace_id,无法与响应 traceId 关联"
    )


def test_task_trace_context_sets_and_resets() -> None:
    with task_trace_context("r-1"):
        assert get_request_id() == "r-1"
    # 退出后 ctx 重置 → 回落到新 uuid(不再是 r-1),避免 prefork 子进程里 trace_id 串味
    assert get_request_id() != "r-1"


def test_task_trace_context_none_is_passthrough() -> None:
    # 无 request_id 不应抛错,也不固定 ctx
    with task_trace_context(None):
        assert isinstance(get_request_id(), str)
