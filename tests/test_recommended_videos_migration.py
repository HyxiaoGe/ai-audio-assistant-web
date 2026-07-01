from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_recommended_video_model_has_expected_columns() -> None:
    from app.models.youtube_recommended_video import YouTubeRecommendedVideo

    assert YouTubeRecommendedVideo.__tablename__ == "youtube_recommended_videos"
    cols = set(YouTubeRecommendedVideo.__table__.columns.keys())
    expected = {
        "id",
        "rank",
        "video_id",
        "title",
        "channel",
        "channel_id",
        "handle",
        "thumbnail",
        "url",
        "view_count",
        "duration",
        "harvested_at",
        "created_at",
        "updated_at",
    }
    assert expected <= cols


def test_new_migration_is_single_head_chaining_off_allowlist() -> None:
    sd = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = sd.get_heads()
    assert len(heads) == 1, f"alembic 出现多 head:{heads}"
    head = sd.get_revision(heads[0])
    assert head.down_revision == "c9d8e7f6a5b4"  # 挂在当前单 head 之下
