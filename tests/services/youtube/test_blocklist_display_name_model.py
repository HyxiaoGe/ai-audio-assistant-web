from sqlalchemy import UniqueConstraint

from app.models.youtube_blocklist import YouTubeBlocklistEntry


def test_display_name_column_exists_nullable_256() -> None:
    col = YouTubeBlocklistEntry.__table__.columns.get("display_name")
    assert col is not None, "display_name 列缺失"
    assert col.nullable is True
    assert col.type.length == 256


def test_display_name_not_in_unique_constraint() -> None:
    uqs = [c for c in YouTubeBlocklistEntry.__table__.constraints if isinstance(c, UniqueConstraint)]
    cols = {c.name for uq in uqs for c in uq.columns}
    # 唯一键必须保持 (kind, match_field, normalized_value) 三列,绝不含 display_name
    assert cols == {"kind", "match_field", "normalized_value"}
