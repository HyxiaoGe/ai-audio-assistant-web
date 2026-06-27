from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode
from app.models.youtube_blocklist import YouTubeBlocklistEntry
from app.services.youtube.search_cache import normalize_query
from app.services.youtube.search_service import VideoHit

# YouTube 频道 ID:UC + 22 个 [A-Za-z0-9_-]。大小写敏感,判型/存储一律原样。
_CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
_CHANNEL_URL_RE = re.compile(r"/channel/(UC[0-9A-Za-z_-]{22})")
# @handle:现代 YouTube 频道的主流标识(youtube.com/@xxx 或裸 @xxx)。大小写不敏感。
# 主机边界:定宽负向 lookbehind + 有界子域可选组(不用 (?:..)* 嵌套量词,避免多项式 ReDoS),
# 既排除 notyoutube.com/@x 误判,又覆盖 www./m./music. 子域。
_HANDLE_URL_RE = re.compile(r"(?<![\w.-])(?:www\.|m\.|music\.)?youtube\.com/@([^/?#\s]+)", re.IGNORECASE)
_BARE_HANDLE_RE = re.compile(r"^@([^/?#\s]+)$")
# 合法 handle 字符集(归一化后):unicode 字母/数字/下划线 + . -,不含 / ? # @ 空格 等。
_HANDLE_CHARS_RE = re.compile(r"^[\w.-]+$")


@dataclass(frozen=True)
class Blocklist:
    terms: frozenset[str]  # 归一化搜索词(env ∪ DB)
    channel_ids: frozenset[str]  # 精确频道 ID(原样)
    channel_names: frozenset[str]  # 归一化频道名
    channel_handles: frozenset[str] = frozenset()  # 归一化 @handle(无 @、casefold)


# ---- 进程内 TTL 缓存 ----
_cache: Blocklist | None = None
_cache_expires_at: datetime | None = None


def invalidate_cache() -> None:
    """admin 写操作后调用,清当前进程缓存(同进程即时;跨副本 ≤TTL 传播)。"""
    global _cache, _cache_expires_at
    _cache = None
    _cache_expires_at = None


def _env_terms() -> set[str]:
    return {normalize_query(w) for w in settings.YOUTUBE_SEARCH_DENYLIST if w and w.strip()}


async def get_blocklist(db: AsyncSession) -> Blocklist:
    """加载黑名单(env terms ∪ DB 活跃行),30s TTL 进程缓存。"""
    global _cache, _cache_expires_at
    now = datetime.now(UTC)
    if _cache is not None and _cache_expires_at is not None and now < _cache_expires_at:
        return _cache

    rows = (
        await db.execute(
            select(YouTubeBlocklistEntry.match_field, YouTubeBlocklistEntry.normalized_value).where(
                YouTubeBlocklistEntry.deleted_at.is_(None)
            )
        )
    ).all()

    terms = _env_terms()
    channel_ids: set[str] = set()
    channel_names: set[str] = set()
    channel_handles: set[str] = set()
    for match_field, normalized_value in rows:
        if match_field == "query":
            terms.add(normalized_value)
        elif match_field == "channel_id":
            channel_ids.add(normalized_value)
        elif match_field == "channel_handle":
            channel_handles.add(normalized_value)
        elif match_field == "channel_name":
            channel_names.add(normalized_value)

    bl = Blocklist(
        terms=frozenset(terms),
        channel_ids=frozenset(channel_ids),
        channel_names=frozenset(channel_names),
        channel_handles=frozenset(channel_handles),
    )
    _cache = bl
    _cache_expires_at = now + timedelta(seconds=settings.BLOCKLIST_CACHE_TTL_SECONDS)
    return bl


def is_term_blocked(normalized_query: str, bl: Blocklist) -> bool:
    return normalized_query in bl.terms


def normalize_handle(raw: str) -> str:
    """频道 @handle 归一化:URL 解码 → strip → 去前导 @ → casefold(handle 大小写不敏感)。"""
    return unquote(raw).strip().lstrip("@").casefold()


def _extract_handle(s: str) -> str | None:
    """从输入里抽出 @handle(youtube.com/@xxx 链接或裸 @xxx),抽不到返 None。"""
    m = _HANDLE_URL_RE.search(s)
    if m:
        return m.group(1)
    m = _BARE_HANDLE_RE.match(s)
    if m:
        return m.group(1)
    return None


def _resolve_channel_meta_sync(url: str) -> tuple[str | None, str | None]:
    """yt-dlp 抓 (channel_id, channel_name);任何失败返 (None, None)。"""
    from yt_dlp import YoutubeDL

    opts = {
        "extract_flat": True,
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": settings.YOUTUBE_SOCKET_TIMEOUT,
        "playlist_items": "1",  # 只取频道元数据
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return (None, None)
    if not isinstance(info, dict):
        return (None, None)
    cid = info.get("channel_id")
    cid = cid if isinstance(cid, str) and _CHANNEL_ID_RE.match(cid) else None
    name = info.get("channel") or info.get("uploader") or info.get("title")
    name = name.strip() if isinstance(name, str) and name.strip() else None
    return (cid, name)


async def resolve_channel_meta(handle: str) -> tuple[str | None, str | None]:
    """@handle → (规范 channel_id, 频道名);从 handle 拼 youtube.com 规范 URL,杜绝 SSRF。"""
    h = handle.strip().lstrip("@")
    if not h:
        return (None, None)
    url = f"https://www.youtube.com/@{h}"
    return await asyncio.to_thread(_resolve_channel_meta_sync, url)


async def resolve_channel_name_by_id(channel_id: str) -> str | None:
    """channel_id → 频道名(Task 3 回填用);拼 /channel/UCx 规范 URL,杜绝 SSRF。失败返 None。"""
    cid = channel_id.strip()
    if not _CHANNEL_ID_RE.match(cid):
        return None
    url = f"https://www.youtube.com/channel/{cid}"
    _, name = await asyncio.to_thread(_resolve_channel_meta_sync, url)
    return name


def is_channel_blocked(hit: VideoHit, bl: Blocklist) -> bool:
    if hit.channel_id and hit.channel_id in bl.channel_ids:
        return True
    if hit.handle and normalize_handle(hit.handle) in bl.channel_handles:
        return True
    if hit.channel and normalize_query(hit.channel) in bl.channel_names:
        return True
    return False


def filter_hits(hits: list[VideoHit], bl: Blocklist) -> list[VideoHit]:
    return [h for h in hits if not is_channel_blocked(h, bl)]


def classify_channel_input(raw: str) -> tuple[str, str]:
    """把管理员输入判型为 (match_field, normalized_value)。

    UCxxxx 裸 ID 或 .../channel/UCxxxx 链接 → ('channel_id', <原样 ID>);
    @handle 或 youtube.com/@handle 链接 → ('channel_handle', 归一化 handle);
    其余(显示名、/c/、/user/)→ ('channel_name', 归一化名)。

    注:('channel_handle', ...) 是中间态——add_entry 会先尝试解析成 channel_id
    存库(匹配最稳、缓存结果也即时生效),解析失败才落库为 handle 兜新搜索。
    """
    s = raw.strip()
    url_match = _CHANNEL_URL_RE.search(s)
    if url_match:
        return ("channel_id", url_match.group(1))
    if _CHANNEL_ID_RE.match(s):
        return ("channel_id", s)
    handle = _extract_handle(s)
    if handle:
        normalized_handle = normalize_handle(handle)
        # 解码后含 / ? # @ 空格等非法字符 → 不是干净 handle,落回按名匹配(不存垃圾、不喂畸形路径给 yt-dlp)
        if normalized_handle and _HANDLE_CHARS_RE.match(normalized_handle):
            return ("channel_handle", normalized_handle)
    return ("channel_name", normalize_query(s))


async def list_entries(db: AsyncSession) -> list[YouTubeBlocklistEntry]:
    rows = (
        (
            await db.execute(
                select(YouTubeBlocklistEntry)
                .where(YouTubeBlocklistEntry.deleted_at.is_(None))
                .order_by(YouTubeBlocklistEntry.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def add_entry(
    db: AsyncSession,
    *,
    kind: str,
    value: str,
    note: str | None,
    created_by: str | None,
    name: str | None = None,
) -> tuple[YouTubeBlocklistEntry, bool]:
    """加黑名单条目:服务端归一化 + 频道判型 + 幂等/复活去重 + 频道名快照(display_name)。

    name 优先级:显式传入(promote 用 flag.channel_name)> @handle yt-dlp 解析名 > 裸名输入本身。
    term 与"裸 UCID 无名"→ display_name 留空(纯展示,前端回落 raw_value)。
    """
    raw = value.strip()
    if not raw:
        raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="value")

    display_name = name.strip() if name and name.strip() else None

    if kind == "term":
        match_field, normalized_value = "query", normalize_query(raw)
        if not normalized_value:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="value")
        display_name = None  # 屏蔽词无频道名
    else:  # "channel"
        match_field, normalized_value = classify_channel_input(raw)
        if match_field == "channel_handle":
            # 优先把 handle 解析为规范 channel_id;顺手取频道名(解析失败保留 handle 兜底)
            resolved_id, resolved_name = await resolve_channel_meta(normalized_value)
            if resolved_id:
                match_field, normalized_value = "channel_id", resolved_id
                if display_name is None:
                    display_name = resolved_name
        elif display_name is None and match_field == "channel_name":
            display_name = raw  # 裸频道名输入本身就是名字

    existing = (
        await db.execute(
            select(YouTubeBlocklistEntry).where(
                YouTubeBlocklistEntry.kind == kind,
                YouTubeBlocklistEntry.match_field == match_field,
                YouTubeBlocklistEntry.normalized_value == normalized_value,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        if existing.deleted_at is None:
            return existing, False  # 已活跃 → 幂等,created=False
        existing.deleted_at = None
        existing.raw_value = raw
        existing.note = note
        existing.created_by = created_by
        if display_name is not None:
            existing.display_name = display_name  # 仅有名时刷新,不拿空名覆盖既有好名
        await db.commit()
        return existing, True  # 软删复活算正常重新拉黑,created=True

    entry = YouTubeBlocklistEntry(
        kind=kind,
        match_field=match_field,
        raw_value=raw,
        normalized_value=normalized_value,
        note=note,
        created_by=created_by,
        display_name=display_name,
    )
    db.add(entry)
    await db.commit()
    return entry, True


async def delete_entry(db: AsyncSession, entry_id: str) -> bool:
    entry = (
        await db.execute(
            select(YouTubeBlocklistEntry).where(
                YouTubeBlocklistEntry.id == entry_id,
                YouTubeBlocklistEntry.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        return False
    entry.deleted_at = datetime.now(UTC)
    await db.commit()
    return True
