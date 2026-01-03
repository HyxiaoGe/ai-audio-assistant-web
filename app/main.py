from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from app.api.v1.router import api_router
from app.core.exceptions import BusinessError
from app.core.i18n import get_message
from app.core.middleware import LocaleMiddleware, LoggingMiddleware, RequestIDMiddleware
from app.core.response import error
from app.i18n.codes import ErrorCode


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
    )

    app.include_router(api_router)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(LocaleMiddleware)
    app.add_middleware(LoggingMiddleware)

    @app.exception_handler(BusinessError)
    async def business_error_handler(
        request: Request, exc: BusinessError
    ) -> JSONResponse:
        locale = getattr(request.state, "locale", "zh")
        message = get_message(exc.code, locale, **exc.kwargs)
        return error(exc.code.value, message)

    @app.exception_handler(DBAPIError)
    async def database_error_handler(
        request: Request, exc: DBAPIError
    ) -> JSONResponse:
        locale = getattr(request.state, "locale", "zh")
        if "invalid UUID" in str(exc):
            # 无效的ID直接当作资源不存在处理，用户不需要知道ID格式问题
            message = get_message(ErrorCode.TASK_NOT_FOUND, locale)
            return error(ErrorCode.TASK_NOT_FOUND.value, message)
        message = get_message(ErrorCode.DATABASE_SERVICE_ERROR, locale)
        return error(ErrorCode.DATABASE_SERVICE_ERROR.value, message)

    @app.exception_handler(Exception)
    async def general_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        locale = getattr(request.state, "locale", "zh")
        message = get_message(ErrorCode.INTERNAL_SERVER_ERROR, locale)
        return error(ErrorCode.INTERNAL_SERVER_ERROR.value, message)

    return app


app = create_app()
