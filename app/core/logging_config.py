"""结构化日志 + trace_id 关联（P3-1）。

把请求/任务的 trace_id 注入每条 LogRecord,使 API 日志、worker 日志与返回给客户端的 traceId
可串起来排障。trace_id 的来源是 app.core.response 的 contextvar(API 侧由 RequestIDMiddleware
设置;worker 侧由 task_trace_context 在任务入口设置)。

- TraceIdFilter:从 contextvar 读 trace_id 写到 record.trace_id(空则 get_request_id 回落新 uuid)。
- configure_logging:幂等地给 root logger 挂 Filter + 带 trace_id 的 Formatter(API startup /
  Celery after_setup_logger / worker_process_init 都调用)。
- task_trace_context:worker 任务入口用 `with` 把 request_id 写进 contextvar,退出即重置
  (防 prefork 子进程复用时 trace_id 串味)。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from app.core.response import get_request_id, reset_request_id, set_request_id

# 沿用 LoggingMiddleware 既有的 "key=value" 人读风格,追加 trace_id 字段。JSON 结构化输出留待
# 真有日志聚合器(Loki/ELK)消费时再单独评估,不在 P3-1 范围。
_TRACE_FORMAT = "%(asctime)s %(levelname)s %(name)s trace_id=%(trace_id)s %(message)s"


class TraceIdFilter(logging.Filter):
    """把当前 trace_id 注入每条 LogRecord。

    get_request_id() 在 contextvar 为空时回落到新的 uuid hex,故 record.trace_id 恒有值,
    引用 %(trace_id)s 的 Formatter 永远不会 KeyError。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_request_id()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """幂等地给 root logger 装上 TraceIdFilter + trace_id Formatter。

    幂等是硬要求:uvicorn --reload、测试里每个用例 create_app()、Celery prefork 子进程回收
    (worker_max_tasks_per_child=100)都会重复调用,绝不能叠加 handler(否则日志重复)。
    实现:不新增 handler(除非 root 一个都没有),只给现有 handler 补挂一次 Filter 并统一 Formatter。
    本函数在 app/worker 启动早期调用——任何内部装配异常都吞掉(退化为无 trace_id 但能正常启动),
    绝不让日志配置失败拖垮整个进程启动。
    """
    try:
        root = logging.getLogger()
        root.setLevel(level)

        if not root.handlers:
            root.addHandler(logging.StreamHandler())

        formatter = logging.Formatter(_TRACE_FORMAT)
        for handler in root.handlers:
            if not any(isinstance(f, TraceIdFilter) for f in handler.filters):
                handler.addFilter(TraceIdFilter())
            handler.setFormatter(formatter)
    except Exception:  # noqa: BLE001 — 日志装配失败不应阻断启动
        logging.getLogger(__name__).warning(
            "configure_logging failed; continuing without trace_id formatting", exc_info=True
        )


@contextmanager
def task_trace_context(request_id: str | None) -> Iterator[None]:
    """worker 任务入口用:把 request_id 写进 trace_id contextvar,退出即重置。

    request_id 为空时是纯 pass-through(不固定 ctx,日志各行回落到各自的 uuid)。重置很关键——
    prefork 子进程会复用,不重置会把上一个任务的 trace_id 串给下一个。
    """
    token = set_request_id(request_id) if request_id else None
    try:
        yield
    finally:
        if token is not None:
            reset_request_id(token)
