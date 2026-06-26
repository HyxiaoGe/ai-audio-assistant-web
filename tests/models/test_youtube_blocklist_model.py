from app.models.youtube_blocklist import YouTubeBlocklistEntry


def test_youtube_blocklist_table_shape() -> None:
    table = YouTubeBlocklistEntry.__table__
    assert table.name == "youtube_blocklist"
    cols = set(table.columns.keys())
    assert {
        "id",
        "kind",
        "match_field",
        "raw_value",
        "normalized_value",
        "note",
        "created_by",
        "created_at",
        "updated_at",
        "deleted_at",  # BaseModel(TimestampMixin) 才有的软删列
    } <= cols
    # match_field 必须 NOT NULL(term 哨兵 'query';否则 NULL 击穿唯一约束)
    assert table.columns["match_field"].nullable is False
    assert table.columns["note"].nullable is True
    unique_names = {c.name for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"}
    assert "uk_youtube_blocklist_entry" in unique_names
