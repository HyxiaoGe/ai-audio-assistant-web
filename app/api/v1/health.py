import asyncio

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_db
from app.core.exceptions import BusinessError
from app.core.redis import get_redis_client
from app.core.response import success
from app.i18n.codes import ErrorCode

router = APIRouter()


@router.get("/health")
async def health() -> JSONResponse:
    """Liveness:进程存活即绿。依赖探活见 /readiness。"""
    return success(data={"status": "ok"})


async def _check_postgres(db: AsyncSession) -> bool:
    try:
        await db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _check_redis() -> bool:
    try:
        await get_redis_client().ping()
        return True
    except Exception:
        return False


async def _check_celery() -> bool:
    # control.ping 是经 Redis broadcast 的同步阻塞调用;to_thread + 短超时,
    # 无 worker 时返回空(超时)也不卡事件循环/部署门。
    try:
        from worker.celery_app import celery_app

        replies = await asyncio.to_thread(celery_app.control.ping, timeout=1)
        return bool(replies)
    except Exception:
        return False


@router.get("/readiness")
async def readiness(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Readiness:Postgres/Redis/Celery 任一不可达即 503(带 per-dep 明细)。

    部署门指向此端点才是真 smoke test;/health 仅作 liveness。不复用 HealthChecker
    (它探外部 ASR/LLM provider API,flaky 且烧钱/吃配额)。
    """
    checks = {
        "postgres": await _check_postgres(db),
        "redis": await _check_redis(),
        "celery": await _check_celery(),
    }
    ready = all(checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


@router.get("/test-error")
async def test_error() -> JSONResponse:
    raise BusinessError(ErrorCode.TASK_NOT_FOUND)


@router.get("/test-auth")
async def test_auth(user: CurrentUser = Depends(get_current_user)) -> JSONResponse:
    return success(data={"user_id": user.id})
