from __future__ import annotations

from pydantic import BaseModel, Field


class AuthSyncRequest(BaseModel):
    provider: str = Field(min_length=1)
    provider_account_id: str = Field(min_length=1)
    email: str = Field(min_length=1)
    name: str | None = None
    avatar_url: str | None = None


class AuthSyncResponse(BaseModel):
    user_id: str
