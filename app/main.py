from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from app.api.v1.router import api_router
from app.core.exceptions import BusinessError
from app.core.i18n import get_message
from app.core.middleware import LocaleMiddleware, LoggingMiddleware, RequestIDMiddleware
from app.core.monitoring import MonitoringSystem
from app.core.response import error
from app.core.smart_factory import SelectionStrategy, SmartFactory, SmartFactoryConfig
from app.i18n.codes import ErrorCode
from app.services.asr import aliyun  # noqa: F401
from app.services.asr import tencent  # noqa: F401
from app.services.asr import volcengine  # noqa: F401
from app.services.asr import configs as asr_configs  # noqa: F401

# 导入所有服务模块以触发 @register_service 装饰器
# 必须在模块顶层导入，而不是在函数内部，这样装饰器才会正确执行
from app.services.llm import configs as llm_configs  # noqa: F401
from app.services.llm import deepseek, doubao, moonshot, openrouter, qwen  # noqa: F401
from app.services.storage import configs as storage_configs  # noqa: F401
from app.services.storage import cos, minio, oss, tos  # noqa: F401


def create_app() -> FastAPI:
    app = FastAPI(title="AI Audio Assistant API", version="0.1.0")

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Type"],
    )

    app.include_router(api_router)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(LocaleMiddleware)
    app.add_middleware(LoggingMiddleware)

    @app.on_event("startup")
    async def startup_event() -> None:
        """Initialize services on application startup."""
        SmartFactory.configure(
            SmartFactoryConfig(
                default_strategy=SelectionStrategy.HEALTH_FIRST,
                enable_monitoring=True,
                enable_fault_tolerance=True,
            )
        )
        MonitoringSystem.get_instance().start()

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        MonitoringSystem.get_instance().stop()

    @app.exception_handler(BusinessError)
    async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
        locale = getattr(request.state, "locale", "zh")
        message = get_message(exc.code, locale, **exc.kwargs)
        return error(exc.code.value, message)

    @app.exception_handler(DBAPIError)
    async def database_error_handler(request: Request, exc: DBAPIError) -> JSONResponse:
        locale = getattr(request.state, "locale", "zh")
        if "invalid UUID" in str(exc):
            # 无效的ID直接当作资源不存在处理，用户不需要知道ID格式问题
            message = get_message(ErrorCode.TASK_NOT_FOUND, locale)
            return error(ErrorCode.TASK_NOT_FOUND.value, message)
        message = get_message(ErrorCode.DATABASE_SERVICE_ERROR, locale)
        return error(ErrorCode.DATABASE_SERVICE_ERROR.value, message)

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        import logging

        logger = logging.getLogger(__name__)

        # 记录详细的异常信息
        logger.exception(
            f"Unhandled exception in {request.method} {request.url.path}: "
            f"{exc.__class__.__name__}: {str(exc)}"
        )

        locale = getattr(request.state, "locale", "zh")
        message = get_message(ErrorCode.INTERNAL_SERVER_ERROR, locale)
        return error(ErrorCode.INTERNAL_SERVER_ERROR.value, message)

    return app


app = create_app()
