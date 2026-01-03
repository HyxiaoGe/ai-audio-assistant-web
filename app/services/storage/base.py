from __future__ import annotations

from abc import ABC, abstractmethod


class StorageService(ABC):
    @abstractmethod
    def presign_put_object(self, object_name: str, expires_in: int) -> str:
        raise NotImplementedError

    @abstractmethod
    def generate_presigned_url(self, object_name: str, expires_in: int) -> str:
        raise NotImplementedError

    @abstractmethod
    def upload_file(
        self, object_name: str, file_path: str, content_type: str | None = None
    ) -> None:
        raise NotImplementedError
