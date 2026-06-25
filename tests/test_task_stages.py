"""task_stages.get_stage_flow 契约测试

合约：
  - source_type="youtube" → YOUTUBE_STAGE_FLOW
  - source_type="url"     → YOUTUBE_STAGE_FLOW（运行 YouTube 管线，stage 必须匹配）
  - source_type="upload"  → AUDIO_STAGE_FLOW（及其它非 youtube/url 值）
"""

from __future__ import annotations

from app.core.task_stages import AUDIO_STAGE_FLOW, YOUTUBE_STAGE_FLOW, get_stage_flow


def test_get_stage_flow_youtube_returns_youtube_flow() -> None:
    """youtube 任务应走 YOUTUBE_STAGE_FLOW（含 RESOLVE_YOUTUBE/DOWNLOAD/TRANSCODE）。"""
    assert get_stage_flow("youtube") == YOUTUBE_STAGE_FLOW


def test_get_stage_flow_url_returns_youtube_flow() -> None:
    """url 任务派发给 process_youtube worker，必须走 YOUTUBE_STAGE_FLOW，否则阶段不匹配。"""
    assert get_stage_flow("url") == YOUTUBE_STAGE_FLOW


def test_get_stage_flow_upload_returns_audio_flow() -> None:
    """upload 任务走 AUDIO_STAGE_FLOW（无下载/解析阶段）。"""
    assert get_stage_flow("upload") == AUDIO_STAGE_FLOW


def test_get_stage_flow_unknown_falls_back_to_audio_flow() -> None:
    """未知 source_type 回退到 AUDIO_STAGE_FLOW，不应抛出。"""
    assert get_stage_flow("unknown_type") == AUDIO_STAGE_FLOW
