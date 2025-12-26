from fastapi import APIRouter

from app.api.v1 import health, tasks, upload, ws

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(upload.router)
api_router.include_router(tasks.router)
api_router.include_router(ws.router)
