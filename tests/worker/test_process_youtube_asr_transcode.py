"""YouTube 链路送 ASR 前的「抽音轨转 16k 单声道 mp3」回归测试。

动机：云 ASR 对「URL 拉取的单文件」普遍有 100MB 上限（阿里云 FlashRecognizer 上游返回
`File too large! (... > 104857600)`）。YouTube 路径原先转无损 WAV（16k mono pcm_s16le≈32KB/s），
1h 视频即 ~145MB 撞线导致整任务 failed；改 mp3 48k 后单文件大幅压小，可用时长从 ~55 分钟
拉到 ~4.7 小时（与 process_audio 上传链路同口径）。这里验证转码产物属性，以及 source_key
扩展名随转码产物变为 .mp3（供 aliyun._guess_format 正确给出 format=mp3）。
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from worker.tasks import process_youtube

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")
_needs_ffmpeg = pytest.mark.skipif(_FFMPEG is None or _FFPROBE is None, reason="需要 ffmpeg/ffprobe 才能验证转码")


def _make_tone(path: str, seconds: int) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", path],
        capture_output=True,
        check=True,
    )


def _probe(path: str, entry: str) -> str:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            entry,
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


@_needs_ffmpeg
def test_transcode_to_mp3_16k_is_mono_16k_and_smaller(tmp_path) -> None:
    # 用未压缩 wav 模拟下载物，确认转码产物为 16k 单声道 mp3 且明显更小
    src = str(tmp_path / "download.wav")
    _make_tone(src, seconds=3)

    out = process_youtube._transcode_to_mp3_16k(src)

    assert out.endswith(".mp3")
    assert os.path.exists(out)
    assert os.path.getsize(out) > 0
    assert _probe(out, "stream=codec_name") == "mp3"
    assert _probe(out, "stream=sample_rate") == "16000"
    assert _probe(out, "stream=channels") == "1"
    # 压缩后应明显小于无损原始 —— 这正是绕开云 ASR 100MB 单文件上限的关键
    assert os.path.getsize(out) < os.path.getsize(src)


@_needs_ffmpeg
def test_transcode_failure_raises_business_error(tmp_path) -> None:
    from app.core.exceptions import BusinessError
    from app.i18n.codes import ErrorCode

    bad = str(tmp_path / "not-audio.webm")
    with open(bad, "wb") as f:
        f.write(b"this is not a media file")

    with pytest.raises(BusinessError) as exc:
        process_youtube._transcode_to_mp3_16k(bad)
    assert exc.value.code == ErrorCode.FILE_PROCESSING_ERROR


def test_build_file_key_propagates_mp3_suffix() -> None:
    # 转码产物为 .mp3 → source_key 也须以 .mp3 结尾，aliyun._guess_format 才会给出 format=mp3
    key = process_youtube._build_file_key("/tmp/dl/abc.mp3", "user-1")
    assert key.startswith("youtube/user-1/")
    assert key.endswith(".mp3")
