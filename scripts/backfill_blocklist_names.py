"""一次性脚本:回填 youtube_blocklist.display_name(本地索引 + yt-dlp 兜底)。

用法(容器内):python -m scripts.backfill_blocklist_names
best-effort:取不到名的条目留空,前端回落 raw_value。
"""

from __future__ import annotations

import asyncio

from app.db import async_session_factory
from app.services.youtube import blocklist_backfill


async def _main() -> None:
    async with async_session_factory() as db:
        stats = await blocklist_backfill.backfill_display_names(db)
    print(f"backfill done: {stats}")


if __name__ == "__main__":
    asyncio.run(_main())
