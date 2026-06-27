from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_admin_user, get_db
from app.core.exceptions import BusinessError
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.schemas.youtube_blocklist import BlocklistEntryCreate, BlocklistEntryOut, BlocklistListOut
from app.services.youtube import blocklist_service

router = APIRouter(prefix="/admin", tags=["youtube-blocklist"])


@router.get("/youtube-blocklist")
async def list_youtube_blocklist(
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(get_admin_user),
) -> JSONResponse:
    entries = await blocklist_service.list_entries(db)
    data = BlocklistListOut(items=[BlocklistEntryOut.model_validate(e) for e in entries])
    return success(data=jsonable_encoder(data))


@router.post("/youtube-blocklist")
async def add_youtube_blocklist(
    body: BlocklistEntryCreate,
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(get_admin_user),
) -> JSONResponse:
    entry, created = await blocklist_service.add_entry(
        db, kind=body.kind, value=body.value, note=body.note, created_by=admin.id
    )
    if not created:
        raise BusinessError(ErrorCode.BLOCKLIST_ENTRY_EXISTS)
    blocklist_service.invalidate_cache()
    return success(data=jsonable_encoder(BlocklistEntryOut.model_validate(entry)))


@router.delete("/youtube-blocklist/{entry_id}")
async def delete_youtube_blocklist(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(get_admin_user),
) -> JSONResponse:
    deleted = await blocklist_service.delete_entry(db, entry_id)
    if not deleted:
        raise BusinessError(ErrorCode.RESOURCE_NOT_FOUND)
    blocklist_service.invalidate_cache()
    return success(message="已删除")
