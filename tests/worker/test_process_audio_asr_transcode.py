"""上传链路送 ASR 前的「抽音轨转 16k 单声道 mp3」回归测试。

动机：云 ASR 对「URL 拉取的单文件」普遍有 100MB 上限（如阿里云 FlashRecognizer 上游返回
`File too large! (... > 104857600)`），而用户可上传至 500MB。process_audio 现在在 extracting
阶段把音轨压成紧凑 mp3 再送 ASR（原始上传对象保留供前端播放）。这里验证键派生与转码产物属性。
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from worker.tasks import process_audio

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")
_needs_ffmpeg = pytest.mark.skipif(_FFMPEG is None or _FFPROBE is None, reason="需要 ffmpeg/ffprobe 才能验证转码")


@pytest.mark.parametrize(
    ("source_key", "expected"),
    [
        ("upload/user-1/2026/05/30/" + "a" * 32 + ".mp4", "upload/user-1/2026/05/30/" + "a" * 32 + ".asr16k.mp3"),
        ("upload/u/2026/05/30/clip.wav", "upload/u/2026/05/30/clip.asr16k.mp3"),
        ("upload/u/2026/05/30/clip.m4a", "upload/u/2026/05/30/clip.asr16k.mp3"),
        # 无扩展名：直接追加
        ("upload/u/2026/05/30/clip", "upload/u/2026/05/30/clip.asr16k.mp3"),
    ],
)
def test_derive_asr_audio_key(source_key: str, expected: str) -> None:
    assert process_audio._derive_asr_audio_key(source_key) == expected


def test_derive_asr_audio_key_stays_in_source_prefix() -> None:
    # ASR 派生键必须与原 key 同前缀同目录，仅换扩展名，避免越权/错位
    src = "upload/user-1/2026/05/30/" + "f" * 32 + ".mov"
    out = process_audio._derive_asr_audio_key(src)
    assert out.startswith("upload/user-1/2026/05/30/")
    assert out.endswith(".asr16k.mp3")
    assert out.rsplit("/", 1)[0] == src.rsplit("/", 1)[0]


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
    src = str(tmp_path / "src.wav")
    _make_tone(src, seconds=3)

    out = process_audio._transcode_to_mp3_16k(src)

    import os

    assert os.path.exists(out)
    assert os.path.getsize(out) > 0
    # 16k 单声道 mp3
    assert _probe(out, "stream=codec_name") == "mp3"
    assert _probe(out, "stream=sample_rate") == "16000"
    assert _probe(out, "stream=channels") == "1"
    # 压缩后应明显小于原始（哪怕 3s tone 也成立）
    assert os.path.getsize(out) < os.path.getsize(src)


@_needs_ffmpeg
def test_transcode_failure_raises_business_error(tmp_path) -> None:
    from app.core.exceptions import BusinessError
    from app.i18n.codes import ErrorCode

    bad = str(tmp_path / "not-audio.mp4")
    with open(bad, "wb") as f:
        f.write(b"this is not a media file")

    with pytest.raises(BusinessError) as exc:
        process_audio._transcode_to_mp3_16k(bad)
    assert exc.value.code == ErrorCode.FILE_PROCESSING_ERROR


@_needs_ffmpeg
def test_probe_duration_seconds(tmp_path) -> None:
    src = str(tmp_path / "tone.wav")
    _make_tone(src, seconds=3)
    assert process_audio._probe_duration_seconds(src) == 3


def test_probe_duration_seconds_bad_path_returns_none() -> None:
    assert process_audio._probe_duration_seconds("/nonexistent/none.wav") is None
