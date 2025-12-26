from __future__ import annotations

from pydantic import BaseModel, Field


class UploadPresignRequest(BaseModel):
    filename: str = Field(min_length=1)
    content_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=1)
    content_hash: str = Field(min_length=1)
