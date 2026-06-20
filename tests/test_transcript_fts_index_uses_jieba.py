"""转写全文检索索引必须用 pg_jieba 的 jiebacfg 中文分词配置,而非 'simple'。

'simple' 不切分中文:to_tsvector('simple','我去北京爬长城') 整句=1 个 token,
长城/北京 等检索一律不命中,这条 GIN 索引对中文形同虚设(dev 实证:'simple' 下
@@ '长城' 返回 false,jiebacfg 下返回 true)。索引表达式必须与搜索端点查询所用
配置一致,planner 才能用上该 GIN 索引。
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.models.transcript import Transcript


def _fts_index_ddl() -> str:
    for idx in Transcript.__table__.indexes:
        if idx.name == "idx_transcripts_fts":
            return str(CreateIndex(idx).compile(dialect=postgresql.dialect())).lower()
    raise AssertionError("未找到 idx_transcripts_fts 索引")


def test_fts_index_uses_jiebacfg_not_simple() -> None:
    ddl = _fts_index_ddl()
    assert "jiebacfg" in ddl, f"FTS 索引未使用 jiebacfg 中文分词: {ddl}"
    assert "'simple'" not in ddl, f"FTS 索引仍在用对中文无效的 'simple' 配置: {ddl}"


def test_fts_index_is_gin_on_to_tsvector() -> None:
    ddl = _fts_index_ddl()
    assert "using gin" in ddl
    assert "to_tsvector" in ddl
