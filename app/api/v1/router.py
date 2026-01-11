from fastapi import APIRouter

from app.api.v1 import (
    asr_quotas,
    auth,
    config_center,
    health,
    llm,
    media,
    notifications,
    stats,
    summaries,
    tasks,
    transcripts,
    upload,
    users,
    ws,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(asr_quotas.router)
api_router.include_router(config_center.router)
api_router.include_router(health.router)
api_router.include_router(llm.router)
api_router.include_router(upload.router)
api_router.include_router(tasks.router)
api_router.include_router(transcripts.router)
api_router.include_router(summaries.router)
api_router.include_router(users.router)
api_router.include_router(notifications.router)
api_router.include_router(stats.router)
api_router.include_router(ws.router)
api_router.include_router(media.router, prefix="/media", tags=["media"])
