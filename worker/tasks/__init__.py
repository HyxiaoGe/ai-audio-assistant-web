from __future__ import annotations

# Import all task modules so Celery can discover them
from worker.tasks import download_youtube  # noqa: F401
from worker.tasks import process_audio  # noqa: F401
from worker.tasks import process_youtube  # noqa: F401

__all__ = ["download_youtube", "process_audio", "process_youtube"]
