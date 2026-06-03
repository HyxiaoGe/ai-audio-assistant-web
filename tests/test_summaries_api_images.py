from __future__ import annotations

from datetime import UTC, datetime

from app.api.v1.summaries import _to_summary_item
from app.models.summary import Summary


def _summary(images) -> Summary:
    s = Summary(
        task_id="t1",
        summary_type="overview",
        version=1,
        is_active=True,
        content="正文 {{IMAGE: a | x | y}}",
        model_used="m",
    )
    s.images = images
    s.created_at = datetime.now(UTC)
    return s


def test_to_summary_item_passes_through_images() -> None:
    images = [
        {
            "placeholder": "{{IMAGE: a | x | y}}",
            "status": "ready",
            "url": "/api/v1/summaries/images/u/t/h.png",
            "alt": "x",
            "model_id": "m",
            "error": None,
        }
    ]
    item = _to_summary_item(_summary(images), image_url=None)
    assert item.images is not None
    assert item.images[0].status == "ready"
    assert item.images[0].placeholder == "{{IMAGE: a | x | y}}"


def test_to_summary_item_images_none_when_absent() -> None:
    item = _to_summary_item(_summary(None), image_url=None)
    assert item.images is None
