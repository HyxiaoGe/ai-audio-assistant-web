from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.deps import get_current_user
from app.core.exceptions import BusinessError
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.models.user import User

router = APIRouter()


@router.get("/health")
async def health() -> JSONResponse:
    return success(data={"status": "ok"})


@router.get("/test-error")
async def test_error() -> JSONResponse:
    raise BusinessError(ErrorCode.TASK_NOT_FOUND)


@router.get("/test-auth")
async def test_auth(user: User = Depends(get_current_user)) -> JSONResponse:
    return success(data={"user_id": user.id})
