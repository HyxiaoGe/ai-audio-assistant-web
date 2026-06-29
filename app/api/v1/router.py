from fastapi import APIRouter

from app.api.v1 import (
    admin_costs,
    asr_quotas,
    asr_usage,
    client_errors,
    config_center,
    health,
    llm,
    media,
    notifications,
    public,
    stats,
    summaries,
    summary_styles,
    tasks,
    transcripts,
    upload,
    users,
    ws,
    youtube,
    youtube_allowlist,
    youtube_blocklist,
    youtube_flagged,
    youtube_search,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(admin_costs.router)
api_router.include_router(asr_quotas.router)
api_router.include_router(asr_usage.router)
api_router.include_router(client_errors.router)
api_router.include_router(config_center.router)
api_router.include_router(health.router)
api_router.include_router(llm.router)
api_router.include_router(upload.router)
api_router.include_router(tasks.router)
api_router.include_router(transcripts.router)
api_router.include_router(summaries.router)
api_router.include_router(summary_styles.router)
api_router.include_router(users.router)
api_router.include_router(notifications.router)
api_router.include_router(public.router)
api_router.include_router(stats.router)
api_router.include_router(ws.router)
api_router.include_router(media.router, prefix="/media", tags=["media"])
api_router.include_router(youtube.router)
api_router.include_router(youtube_allowlist.router)
api_router.include_router(youtube_blocklist.router)
api_router.include_router(youtube_flagged.router)
api_router.include_router(youtube_search.router)
