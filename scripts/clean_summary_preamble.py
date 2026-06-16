#!/usr/bin/env python3
"""存量清洗:剥掉 Summary.content 开头逸出的客套/元描述开场白。

历史上 LLM 偶发把开场白(如「好的，这是为您生成的摘要。」「Sure, here's
the summary…」)写进 Summary.content 开头并落库,污染了列表卡 excerpt 与
详情正文。新生成路径已在源头剥(strip_summary_preamble),本脚本清洗存量。

只处理文本类摘要(overview / key_points / action_items);chapters 等
JSON 类型(content 存 json.dumps 结果)一律跳过。复用与生成路径同一份
strip_summary_preamble,行为一致且幂等(再跑一次命中 0)。

Usage:
    # 1) 先 dry-run 看命中行数 + 抽样 before/after 对照,不写库(默认)
    python scripts/clean_summary_preamble.py

    # 2) 确认无误后再真正写库(UPDATE 命中行)
    python scripts/clean_summary_preamble.py --apply

    # 可选:抽样条数(默认 10)
    python scripts/clean_summary_preamble.py --sample 20

依赖 DATABASE_URL(同 app 配置)。dry-run 全程只读,绝不写库。
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select

from app.db import async_session_factory
from app.models.summary import Summary
from app.services.summary.preamble import strip_summary_preamble

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("clean_summary_preamble")

# 仅文本类摘要;chapters / 其它 JSON-in-content 类型跳过。
TEXT_SUMMARY_TYPES = ("overview", "key_points", "action_items")
_PREVIEW = 60


def _preview(text: str) -> str:
    """单行预览前 60 字(折叠换行,便于对照)。"""
    flat = text.replace("\n", "\\n")
    return flat[:_PREVIEW] + ("…" if len(flat) > _PREVIEW else "")


async def run(apply: bool, sample: int) -> None:
    mode = "APPLY(写库)" if apply else "DRY-RUN(只读)"
    log.info("清洗模式:%s,摘要类型:%s", mode, TEXT_SUMMARY_TYPES)

    hits: list[tuple[str, str, str]] = []  # (summary_id, before, after)

    async with async_session_factory() as session:
        stmt = select(Summary).where(
            Summary.summary_type.in_(TEXT_SUMMARY_TYPES),
            Summary.content.isnot(None),
            Summary.content != "",
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        log.info("扫描文本类摘要 %d 行", len(rows))

        for summary in rows:
            content = summary.content
            new = strip_summary_preamble(content)
            if new != content:
                hits.append((str(summary.id), content, new))
                if apply:
                    summary.content = new

        log.info("命中(开场白可剥)行数:%d", len(hits))

        # 抽样 before/after 对照(前 60 字)
        for sid, before, after in hits[:sample]:
            log.info("  id=%s", sid)
            log.info("    before: %s", _preview(before))
            log.info("    after : %s", _preview(after))

        if not apply:
            log.info("DRY-RUN 结束,未写库。确认无误后加 --apply 执行。")
            return

        if hits:
            await session.commit()
            log.info("已写库:UPDATE %d 行。", len(hits))
        else:
            log.info("无命中,无需写库。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="存量清洗 Summary.content 开头的客套/元描述开场白(默认 dry-run)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正写库(默认仅 dry-run 预览,不写库)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=10,
        help="抽样打印的 before/after 对照条数(默认 10)",
    )
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply, sample=args.sample))


if __name__ == "__main__":
    main()
