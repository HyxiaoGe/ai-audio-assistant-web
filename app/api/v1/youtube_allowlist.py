from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_admin_user, get_db
from app.core.exceptions import BusinessError
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.schemas.youtube_allowlist import AllowlistEntryCreate, AllowlistEntryOut, AllowlistListOut
from app.services.youtube import allowlist_service

router = APIRouter(prefix="/admin", tags=["youtube-allowlist"])


@router.get("/youtube-allowlist")
async def list_youtube_allowlist(
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(get_admin_user),
) -> JSONResponse:
    entries = await allowlist_service.list_entries(db)
    data = AllowlistListOut(items=[AllowlistEntryOut.model_validate(e) for e in entries])
    return success(data=jsonable_encoder(data))


@router.post("/youtube-allowlist")
async def add_youtube_allowlist(
    body: AllowlistEntryCreate,
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(get_admin_user),
) -> JSONResponse:
    entry, created = await allowlist_service.add_entry(db, value=body.value, note=body.note, created_by=admin.id)
    if not created:
        raise BusinessError(ErrorCode.ALLOWLIST_ENTRY_EXISTS)
    allowlist_service.invalidate_cache()
    return success(data=jsonable_encoder(AllowlistEntryOut.model_validate(entry)))


@router.delete("/youtube-allowlist/{entry_id}")
async def delete_youtube_allowlist(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(get_admin_user),
) -> JSONResponse:
    deleted = await allowlist_service.delete_entry(db, entry_id)
    if not deleted:
        raise BusinessError(ErrorCode.RESOURCE_NOT_FOUND)
    allowlist_service.invalidate_cache()
    return success(message="已删除")
