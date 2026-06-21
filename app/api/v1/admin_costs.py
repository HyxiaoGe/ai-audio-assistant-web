"""管理员成本看板 API。

把分散的三处成本来源按用户归并:ASR(¥,ASRUsage 账本)+ 配图(¥,按张估)+ LLM($,LiteLLM
end-user spend)。解决「管理员看不到每个用户(含自己)花了多少」。

货币分两列呈现、绝不跨币种相加。LiteLLM 来源不可用(无 master key)时,LLM 列降级为 None、
¥ 两列照常 —— 不让 LLM 来源不可用拖垮整表。仅管理员可访问。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_admin_user, get_db
from app.core.response import success
from app.models.user import UserProfile
from app.schemas.admin_costs import AdminCostsResponse, UserCostRow
from app.services.cost.aggregator import asr_cost_by_user, image_cost_by_user
from app.services.cost.pricing import price_for_image_model
from app.services.llm.spend_client import LiteLLMSpendClient

router = APIRouter(prefix="/admin", tags=["admin-costs"])

_CURRENCY_NOTE = "ASR/配图为人民币(¥),LLM 为美元($);两列币种不同,不可相加。"


async def _display_names(db: AsyncSession, user_ids: set[str]) -> dict[str, str | None]:
    if not user_ids:
        return {}
    rows = (
        await db.execute(select(UserProfile.id, UserProfile.display_name).where(UserProfile.id.in_(user_ids)))
    ).all()
    return {str(row.id): row.display_name for row in rows}


@router.get("/costs/by-user")
async def get_costs_by_user(
    start_date: datetime | None = Query(default=None, description="开始时间(含)"),
    end_date: datetime | None = Query(default=None, description="结束时间(含)"),
    db: AsyncSession = Depends(get_db),
    admin: CurrentUser = Depends(get_admin_user),
) -> JSONResponse:
    """按用户聚合成本(管理员)。

    返回每个用户的 ¥(ASR + 配图)与 $(LLM)两列;按 ¥ 合计降序。包含管理员自己的行。
    """
    asr = await asr_cost_by_user(db, start_date, end_date)
    images = await image_cost_by_user(db, price_for_image_model, start_date, end_date)

    # 用户全集:任何经过处理的任务都有 ASR(转写),故 ASR 用户 ⊇ 有 LLM/配图的用户;并上配图用户兜底。
    user_ids: set[str] = set(asr) | set(images)

    spend_client = LiteLLMSpendClient()
    llm = await spend_client.spend_by_end_user(user_ids)
    names = await _display_names(db, user_ids)

    rows: list[UserCostRow] = []
    for uid in user_ids:
        a = asr.get(uid, {})
        asr_cny = float(a.get("estimated_cny", 0.0))
        image_cny = float(images.get(uid, 0.0))
        rows.append(
            UserCostRow(
                user_id=uid,
                display_name=names.get(uid),
                is_self=(uid == str(admin.id)),
                asr_cny=asr_cny,
                asr_paid_cny=float(a.get("paid_cny", 0.0)),
                asr_calls=int(a.get("calls", 0)),
                image_cny=image_cny,
                cny_total=asr_cny + image_cny,
                llm_usd=(float(llm.get(uid, 0.0)) if spend_client.available else None),
            )
        )

    rows.sort(key=lambda r: r.cny_total, reverse=True)

    response = AdminCostsResponse(
        items=rows,
        llm_source="litellm" if spend_client.available else "unavailable",
        period_start=start_date,
        period_end=end_date,
        currency_note=_CURRENCY_NOTE,
    )
    return success(data=jsonable_encoder(response))
