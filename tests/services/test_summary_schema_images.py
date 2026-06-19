from __future__ import annotations

from datetime import UTC, datetime

from app.models.summary import Summary
from app.schemas.summary import SummaryImageItem, SummaryItem


def test_summary_model_has_images_column() -> None:
    assert "images" in Summary.__table__.columns
    assert Summary.__table__.columns["images"].nullable is True


def test_summary_image_item_shape() -> None:
    item = SummaryImageItem(
        placeholder="{{IMAGE: infographic | 主题 | 关键文字}}",
        status="pending",
        url=None,
        alt="主题",
        model_id=None,
        error=None,
    )
    dumped = item.model_dump()
    # provider 为 PR1(纯 schema 地基)新增的配图溯源字段(JSONB 加 key,无迁移),
    # 形状守卫须随 schema 一并更新,否则锁死旧字段集致 CI 红。
    assert set(dumped) == {
        "placeholder",
        "status",
        "url",
        "alt",
        "model_id",
        "provider",
        "error",
    }
    assert dumped["status"] == "pending"


def test_summary_item_images_defaults_none_and_accepts_list() -> None:
    base = dict(
        id="s1",
        summary_type="overview",
        version=1,
        is_active=True,
        content="正文 {{IMAGE: infographic | 主题 | 关键文字}}",
        created_at=datetime.now(UTC),
    )
    assert SummaryItem(**base).images is None
    with_imgs = SummaryItem(
        **base,
        images=[
            {
                "placeholder": "{{IMAGE: infographic | 主题 | 关键文字}}",
                "status": "ready",
                "url": "/api/v1/summaries/images/u/t/h.png",
                "alt": "主题",
                "model_id": "gemini-3-pro-image-preview",
                "error": None,
            }
        ],
    )
    assert with_imgs.images[0].status == "ready"
    assert with_imgs.images[0].url.endswith("h.png")
