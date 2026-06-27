from app.models.flagged_channel import FlaggedChannel


def test_flagged_channel_tablename() -> None:
    assert FlaggedChannel.__tablename__ == "flagged_channels"


def test_flagged_channel_has_all_columns() -> None:
    cols = set(FlaggedChannel.__table__.columns.keys())
    expected = {
        "id",
        "created_at",
        "updated_at",
        "match_field",
        "match_value",
        "channel_id",
        "channel_handle",
        "channel_name",
        "block_count",
        "last_video_id",
        "last_title",
        "last_flagged_at",
        "status",
        "resolved_by",
        "resolved_at",
    }
    assert expected <= cols
    assert "deleted_at" not in cols  # BaseRecord 无软删


def test_flagged_channel_unique_and_index() -> None:
    uniques = {
        tuple(c.name for c in con.columns)
        for con in FlaggedChannel.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    }
    assert ("match_field", "match_value") in uniques
    index_names = {ix.name for ix in FlaggedChannel.__table__.indexes}
    assert "idx_flagged_channels_pending" in index_names
