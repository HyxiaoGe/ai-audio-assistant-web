"""Media file streaming endpoint.

Proxies media files from storage (MinIO) to frontend, hiding storage implementation details.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.smart_factory import SmartFactory

logger = logging.getLogger("app.api.media")

router = APIRouter()


@router.get("/{file_path:path}")
async def stream_media(file_path: str, request: Request) -> StreamingResponse:
    """Stream media file from storage.

    Args:
        file_path: Relative path to the media file (e.g., "uploads/youtube/2025/12/xxx.wav")
        request: FastAPI request object (for range requests support)

    Returns:
        StreamingResponse with the media file content

    Raises:
        HTTPException: If file not found or access denied
    """
    try:
        from app.config import settings

        # 使用 SmartFactory 获取 MinIO storage
        minio_storage = await SmartFactory.get_service("storage", provider="minio")
        bucket = settings.MINIO_BUCKET
        if not bucket:
            raise HTTPException(status_code=500, detail="Storage not configured")

        # Get file metadata
        try:
            stat = minio_storage._client.stat_object(bucket, file_path)
            file_size = stat.size
            content_type = stat.content_type or "application/octet-stream"
        except Exception as e:
            logger.warning(f"File not found: {file_path}, error: {e}")
            raise HTTPException(status_code=404, detail="File not found")

        # Handle range requests (for video/audio seeking)
        range_header = request.headers.get("range")
        if range_header:
            # Parse range header: "bytes=start-end"
            range_value = range_header.replace("bytes=", "")
            range_parts = range_value.split("-")
            start = int(range_parts[0]) if range_parts[0] else 0
            end = int(range_parts[1]) if len(range_parts) > 1 and range_parts[1] else file_size - 1

            # Get partial content
            response = minio_storage._client.get_object(
                bucket,
                file_path,
                offset=start,
                length=end - start + 1,
            )

            def iter_content():
                try:
                    for chunk in response.stream(8192):
                        yield chunk
                finally:
                    response.close()
                    response.release_conn()

            headers = {
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(end - start + 1),
                "Content-Type": content_type,
            }

            return StreamingResponse(
                iter_content(),
                status_code=206,  # Partial Content
                headers=headers,
                media_type=content_type,
            )
        else:
            # Full file download
            response = minio_storage._client.get_object(bucket, file_path)

            def iter_content():
                try:
                    for chunk in response.stream(8192):
                        yield chunk
                finally:
                    response.close()
                    response.release_conn()

            headers = {
                "Content-Length": str(file_size),
                "Accept-Ranges": "bytes",
                "Content-Type": content_type,
            }

            return StreamingResponse(
                iter_content(),
                headers=headers,
                media_type=content_type,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error streaming media file {file_path}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
