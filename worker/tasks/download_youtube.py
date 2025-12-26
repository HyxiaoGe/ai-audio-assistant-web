from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from yt_dlp import YoutubeDL

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from worker.celery_app import celery_app

logger = logging.getLogger("worker.download_youtube")


def _get_download_dir() -> Path:
    raw_dir = settings.YOUTUBE_DOWNLOAD_DIR
    if not raw_dir:
        raise RuntimeError("YOUTUBE_DOWNLOAD_DIR is not set")
    path = Path(raw_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_output_template() -> str:
    template = settings.YOUTUBE_OUTPUT_TEMPLATE
    if not template:
        raise RuntimeError("YOUTUBE_OUTPUT_TEMPLATE is not set")
    return template


def _get_download_format() -> str:
    fmt = settings.YOUTUBE_DOWNLOAD_FORMAT
    if not fmt:
        raise RuntimeError("YOUTUBE_DOWNLOAD_FORMAT is not set")
    return fmt


def _validate_youtube_url(url: str) -> None:
    if not url:
        raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="source_url")
    lower_url = url.lower()
    if not lower_url.startswith("http"):
        raise BusinessError(ErrorCode.INVALID_URL_FORMAT)
    if "youtube.com" not in lower_url and "youtu.be" not in lower_url:
        raise BusinessError(ErrorCode.UNSUPPORTED_YOUTUBE_URL_FORMAT)


def _download(url: str) -> str:
    _validate_youtube_url(url)
    output_dir = _get_download_dir()
    outtmpl = str(output_dir / _get_output_template())
    fmt = _get_download_format()
    ydl_opts = {
        "format": fmt,
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            return filename
    except Exception as exc:
        logger.exception("youtube download failed: %s", exc)
        raise BusinessError(ErrorCode.YOUTUBE_DOWNLOAD_FAILED, reason=str(exc)) from exc


@celery_app.task(
    name="worker.tasks.download_youtube",
    bind=True,
    max_retries=3,
    soft_time_limit=1800,
    hard_time_limit=2000,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def download_youtube(self, url: str, request_id: Optional[str] = None) -> str:
    return _download(url)
