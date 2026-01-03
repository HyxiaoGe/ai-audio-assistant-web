from __future__ import annotations

from pydantic import BaseModel


class UserProfileResponse(BaseModel):
    id: str
    email: str
    name: str | None = None
    avatar_url: str
