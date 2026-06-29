from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_admin_user, get_db
from app.core.response import success
from app.schemas.youtube_flagged import FlaggedChannelListOut, FlaggedChannelOut, FlagResolveRequest
from app.services.youtube import channel_flag_service

router = APIRouter(prefix="/admin", tags=["youtube-flagged"])


def _to_out(flag: object) -> FlaggedChannelOut:
    return FlaggedChannelOut(
        id=flag.id,
        match_field=flag.match_field,
        match_value=flag.match_value,
        channel_id=flag.channel_id,
        channel_handle=flag.channel_handle,
        channel_name=flag.channel_name,
        block_count=flag.block_count,
        last_video_id=flag.last_video_id,
        last_title=flag.last_title,
        status=flag.status,
        first_flagged_at=flag.created_at,
        last_flagged_at=flag.last_flagged_at,
    )


@router.get("/flagged-channels")
async def list_flagged_channels(
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(get_admin_user),
) -> JSONResponse:
    """复核队列:列 pending 标记,按累计次数降序。"""
    flags = await channel_flag_service.list_pending(db)
    data = FlaggedChannelListOut(items=[_to_out(f) for f in flags])
    return success(data=jsonable_encoder(data))


@router.post("/flagged-channels/{flag_id}/resolve")
async def resolve_flagged_channel(
    flag_id: str,
    body: FlagResolveRequest,
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(get_admin_user),
) -> JSONResponse:
    """复核处置:action=block 提升频道黑名单 / action=dismiss 永久加白。"""
    flag, _ = await channel_flag_service.resolve(
        db, flag_id=flag_id, action=body.action, admin_id=admin.id, note=body.note
    )
    return success(data=jsonable_encoder(_to_out(flag)))
