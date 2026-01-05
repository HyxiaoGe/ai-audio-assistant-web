from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_user, get_current_user, get_db
from app.core.config_manager import ConfigManager
from app.core.exceptions import BusinessError
from app.core.health_checker import HealthChecker
from app.core.response import success
from app.i18n.codes import ErrorCode
from app.models.service_config import ServiceConfig
from app.models.service_config_history import ServiceConfigHistory
from app.models.user import User
from app.schemas.config_center import ConfigRollbackRequest, ConfigUpdateRequest

router = APIRouter(prefix="/configs", tags=["config-center"])


def _serialize_config(record: ServiceConfig) -> dict[str, Any]:
    return {
        "service_type": record.service_type,
        "provider": record.provider,
        "owner_user_id": record.owner_user_id,
        "enabled": record.enabled,
        "version": record.version,
        "config": record.config,
        "updated_by": record.updated_by,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


@router.get("")
async def list_configs(
    service_type: Optional[str] = None,
    provider: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
) -> Any:
    stmt = select(ServiceConfig)
    if service_type:
        stmt = stmt.where(ServiceConfig.service_type == service_type)
    if provider:
        stmt = stmt.where(ServiceConfig.provider == provider)
    records = (await db.execute(stmt)).scalars().all()
    return success(data={"items": [_serialize_config(record) for record in records]})


@router.get("/{service_type}/{provider}")
async def get_config(
    service_type: str,
    provider: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
) -> Any:
    stmt = select(ServiceConfig).where(
        ServiceConfig.service_type == service_type,
        ServiceConfig.provider == provider,
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Config not found")
    return success(data=_serialize_config(record))


@router.put("/{service_type}/{provider}")
async def upsert_config(
    service_type: str,
    provider: str,
    payload: ConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
) -> Any:
    config_data = dict(payload.config)
    config_data["enabled"] = payload.enabled
    try:
        ConfigManager.validate_config_data(service_type, provider, config_data)
    except Exception as exc:
        raise BusinessError(ErrorCode.INVALID_PARAMETER, detail=str(exc)) from exc

    stmt = select(ServiceConfig).where(
        ServiceConfig.service_type == service_type,
        ServiceConfig.provider == provider,
        ServiceConfig.owner_user_id.is_(None),
    )
    record = (await db.execute(stmt)).scalar_one_or_none()

    if record is None:
        record = ServiceConfig(
            service_type=service_type,
            provider=provider,
            config=payload.config,
            enabled=payload.enabled,
            version=1,
            updated_by=user.id,
        )
        db.add(record)
    else:
        history = ServiceConfigHistory(
            service_type=record.service_type,
            provider=record.provider,
            owner_user_id=record.owner_user_id,
            version=record.version,
            config=record.config,
            enabled=record.enabled,
            note=payload.note,
            updated_by=user.id,
        )
        db.add(history)
        record.config = payload.config
        record.enabled = payload.enabled
        record.version = record.version + 1
        record.updated_by = user.id

    await db.commit()
    await db.refresh(record)
    await ConfigManager.refresh_from_db(service_type, provider)
    await HealthChecker.check_service(service_type, provider, force=True)
    return success(data=_serialize_config(record))


@router.post("/{service_type}/{provider}/rollback")
async def rollback_config(
    service_type: str,
    provider: str,
    payload: ConfigRollbackRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_admin_user),
) -> Any:
    stmt = select(ServiceConfig).where(
        ServiceConfig.service_type == service_type,
        ServiceConfig.provider == provider,
        ServiceConfig.owner_user_id.is_(None),
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Config not found")

    history_stmt = select(ServiceConfigHistory).where(
        ServiceConfigHistory.service_type == service_type,
        ServiceConfigHistory.provider == provider,
        ServiceConfigHistory.owner_user_id.is_(None),
    )
    if payload.version:
        history_stmt = history_stmt.where(ServiceConfigHistory.version == payload.version)
    history_stmt = history_stmt.order_by(desc(ServiceConfigHistory.version))
    history = (await db.execute(history_stmt)).scalars().first()
    if history is None:
        raise HTTPException(status_code=404, detail="No history available for rollback")

    db.add(
        ServiceConfigHistory(
            service_type=record.service_type,
            provider=record.provider,
            owner_user_id=record.owner_user_id,
            version=record.version,
            config=record.config,
            enabled=record.enabled,
            note=payload.note or f"rollback to {history.version}",
            updated_by=user.id,
        )
    )
    record.config = history.config
    record.enabled = history.enabled
    record.version = record.version + 1
    record.updated_by = user.id

    await db.commit()
    await db.refresh(record)
    await ConfigManager.refresh_from_db(service_type, provider)
    await HealthChecker.check_service(service_type, provider, force=True)
    return success(data=_serialize_config(record))


@router.post("/refresh")
async def refresh_cache(
    service_type: Optional[str] = None,
    provider: Optional[str] = None,
    _: User = Depends(get_admin_user),
) -> Any:
    await ConfigManager.refresh_from_db(service_type, provider)
    return success(data={"refreshed": True})


@router.get("/me")
async def list_my_configs(
    service_type: Optional[str] = None,
    provider: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    stmt = select(ServiceConfig).where(ServiceConfig.owner_user_id == user.id)
    if service_type:
        stmt = stmt.where(ServiceConfig.service_type == service_type)
    if provider:
        stmt = stmt.where(ServiceConfig.provider == provider)
    records = (await db.execute(stmt)).scalars().all()
    return success(data={"items": [_serialize_config(record) for record in records]})


@router.get("/me/{service_type}/{provider}")
async def get_my_config(
    service_type: str,
    provider: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    stmt = select(ServiceConfig).where(
        ServiceConfig.service_type == service_type,
        ServiceConfig.provider == provider,
        ServiceConfig.owner_user_id == user.id,
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Config not found")
    return success(data=_serialize_config(record))


@router.put("/me/{service_type}/{provider}")
async def upsert_my_config(
    service_type: str,
    provider: str,
    payload: ConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    config_data = dict(payload.config)
    config_data["enabled"] = payload.enabled
    try:
        ConfigManager.validate_config_data(service_type, provider, config_data)
    except Exception as exc:
        raise BusinessError(ErrorCode.INVALID_PARAMETER, detail=str(exc)) from exc

    stmt = select(ServiceConfig).where(
        ServiceConfig.service_type == service_type,
        ServiceConfig.provider == provider,
        ServiceConfig.owner_user_id == user.id,
    )
    record = (await db.execute(stmt)).scalar_one_or_none()

    if record is None:
        record = ServiceConfig(
            service_type=service_type,
            provider=provider,
            owner_user_id=user.id,
            config=payload.config,
            enabled=payload.enabled,
            version=1,
            updated_by=user.id,
        )
        db.add(record)
    else:
        history = ServiceConfigHistory(
            service_type=record.service_type,
            provider=record.provider,
            owner_user_id=record.owner_user_id,
            version=record.version,
            config=record.config,
            enabled=record.enabled,
            note=payload.note,
            updated_by=user.id,
        )
        db.add(history)
        record.config = payload.config
        record.enabled = payload.enabled
        record.version = record.version + 1
        record.updated_by = user.id

    await db.commit()
    await db.refresh(record)
    await ConfigManager.refresh_from_db(service_type, provider, user_id=user.id)
    return success(data=_serialize_config(record))


@router.post("/me/{service_type}/{provider}/rollback")
async def rollback_my_config(
    service_type: str,
    provider: str,
    payload: ConfigRollbackRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Any:
    stmt = select(ServiceConfig).where(
        ServiceConfig.service_type == service_type,
        ServiceConfig.provider == provider,
        ServiceConfig.owner_user_id == user.id,
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Config not found")

    history_stmt = select(ServiceConfigHistory).where(
        ServiceConfigHistory.service_type == service_type,
        ServiceConfigHistory.provider == provider,
        ServiceConfigHistory.owner_user_id == user.id,
    )
    if payload.version:
        history_stmt = history_stmt.where(ServiceConfigHistory.version == payload.version)
    history_stmt = history_stmt.order_by(desc(ServiceConfigHistory.version))
    history = (await db.execute(history_stmt)).scalars().first()
    if history is None:
        raise HTTPException(status_code=404, detail="No history available for rollback")

    db.add(
        ServiceConfigHistory(
            service_type=record.service_type,
            provider=record.provider,
            owner_user_id=record.owner_user_id,
            version=record.version,
            config=record.config,
            enabled=record.enabled,
            note=payload.note or f"rollback to {history.version}",
            updated_by=user.id,
        )
    )
    record.config = history.config
    record.enabled = history.enabled
    record.version = record.version + 1
    record.updated_by = user.id

    await db.commit()
    await db.refresh(record)
    await ConfigManager.refresh_from_db(service_type, provider, user_id=user.id)
    return success(data=_serialize_config(record))
