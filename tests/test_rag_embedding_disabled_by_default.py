"""RAG 止血守卫:embedding 写端默认必须关闭。

读端 100% 不存在(全平台无任何检索/相似度/搜索消费 RagChunk,唯一另一引用是
cleanup_task 的 DELETE),而写端默认开启,在 dev 上 embedding 大半失败
(call_type='embedding' 的 LLMUsage failed 远多于 success),纯浪费延迟+日志+垃圾
usage 行。在真正建成语义检索功能(pgvector + /search 语义路径)之前默认关闭。

注:全文检索走 pg_jieba FTS(to_tsvector('jiebacfg')),与此 embedding RAG 是两条路,
本守卫只针对后者的死写入。
"""

from __future__ import annotations

import pytest

from app.config import Settings


def test_rag_embedding_disabled_by_default() -> None:
    """声明的默认值必须是 False(与 env 覆盖无关,锁住代码默认)。"""
    assert Settings.model_fields["RAG_EMBEDDING_ENABLED"].default is False


def test_build_chunks_short_circuits_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭时 _build_chunks 必须在调用 build_rag_chunks 之前早返回 [],
    保证整条 ingest 不写任何 RagChunk / embedding LLMUsage。"""
    from app.services.rag import ingest

    monkeypatch.setattr(ingest.settings, "RAG_EMBEDDING_ENABLED", False)

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("RAG 关闭时不应进入分块/嵌入路径")

    monkeypatch.setattr(ingest, "build_rag_chunks", _boom)
    assert ingest._build_chunks([object()]) == []
