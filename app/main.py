from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.exceptions import BusinessError
from app.core.i18n import get_message
from app.core.middleware import LocaleMiddleware, LoggingMiddleware, RequestIDMiddleware
from app.core.response import error


def create_app() -> FastAPI:
    app = FastAPI(title="AI Audio Assistant API", version="0.1.0")
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

    return app


app = create_app()
