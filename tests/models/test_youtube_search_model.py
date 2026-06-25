from app.models.youtube_search import YouTubeSearchQuery


def test_youtube_search_query_table_shape() -> None:
    table = YouTubeSearchQuery.__table__
    assert table.name == "youtube_search_queries"
    cols = set(table.columns.keys())
    assert {
        "id",
        "normalized_query",
        "display_query",
        "results_json",
        "fetched_at",
        "search_count",
        "last_searched_at",
        "is_blocked",
        "created_at",
        "updated_at",
    } <= cols
    unique_names = {c.name for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"}
    assert "uk_youtube_search_queries_normalized" in unique_names
