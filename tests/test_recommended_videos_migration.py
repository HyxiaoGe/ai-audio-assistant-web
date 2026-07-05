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
    # recommended_videos(d1e2f3a4b5c6)仍挂在 allowlist(c9d8e7f6a5b4)之下;其后又叠了
    # transcripts.manually_edited(e2f3a4b5c6d7)成为新单 head——故按具体 revision 校验,不再假设它是 head。
    rev = sd.get_revision("d1e2f3a4b5c6")
    assert rev.down_revision == "c9d8e7f6a5b4"
