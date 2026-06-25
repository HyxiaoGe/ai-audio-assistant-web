from __future__ import annotations

from typing import Any

import pytest

from app.models.asr_usage import ASRUsage
from app.models.task import Task
from app.models.transcript import Transcript
from worker.tasks import process_youtube


class _FakeSyncQuery:
    """Minimal chainable stand-in for a SQLAlchemy ``Session.query(...)`` chain."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def filter(self, *args: Any, **kwargs: Any) -> _FakeSyncQuery:
        return self

    def order_by(self, *args: Any, **kwargs: Any) -> _FakeSyncQuery:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeSyncSession:
    """Returns preloaded transcripts / claim rows by queried model, no database."""

    def __init__(self, transcripts: list[Transcript], claim: ASRUsage | None) -> None:
        self._transcripts = transcripts
        self._claim = claim

    def query(self, model: Any) -> _FakeSyncQuery:
        name = getattr(model, "__name__", str(model))
        if name == "Transcript":
            return _FakeSyncQuery(self._transcripts)
        if name == "ASRUsage":
            return _FakeSyncQuery([self._claim] if self._claim is not None else [])
        return _FakeSyncQuery([])


def _task() -> Task:
    return Task(
        user_id="user-1",
        content_hash="hash-1",
        title="demo",
        source_type="youtube",
        source_url="https://example.com/v",
        duration_seconds=120.0,
    )


def _transcript(task: Task) -> Transcript:
    return Transcript(
        task_id=task.id,
        speaker_id="1",
        content="hello",
        start_time=0.0,
        end_time=2.0,
        confidence=0.9,
        sequence=1,
    )


def test_finalize_existing_cost_sync_tolerates_provider_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FINALIZE_COST must record cost even when the ASR service can't be built.

    The provider lookup only feeds the optional ``estimate_cost``. If it raises
    (registry / credential / instantiation failure, force_new=True), the cost
    must still be recorded atomically from the claim's provider/variant -- not
    propagate and autoretry the youtube task into a stuck, never-charged state.
    """
    task = _task()
    claim = ASRUsage(
        user_id=str(task.user_id),
        task_id=str(task.id),
        provider="volcengine",
        variant="file",
        duration_seconds=0.0,
        status="processing",
    )
    session = _FakeSyncSession([_transcript(task)], claim)

    async def _raising_get_service(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("asr service construction failed")

    finalize_calls: list[dict[str, Any]] = []

    def _spy_finalize(*args: Any, **kwargs: Any) -> None:
        finalize_calls.append(kwargs)

    monkeypatch.setattr(process_youtube.SmartFactory, "get_service", _raising_get_service)
    monkeypatch.setattr(process_youtube, "_finalize_asr_cost_sync", _spy_finalize)

    # Must NOT raise even though the provider lookup blows up.
    process_youtube._finalize_existing_transcript_cost_sync(session, task, str(task.id))

    assert len(finalize_calls) == 1
    assert finalize_calls[0]["asr_service"] is None  # fell back; lookup failure swallowed
    assert finalize_calls[0]["provider_name"] == "volcengine"  # cost keyed off the claim
    assert finalize_calls[0]["claim_row"] is claim  # finalized in place, no duplicate row


# --------------------------------------------------------------------------- #
# Phase 2: kind="task_progress" 信封标签断言
# --------------------------------------------------------------------------- #
class _CaptureSyncPublish:
    """捕获 process_youtube.publish_task_update_sync 的 message 以断言信封 kind。"""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, task_id: str, user_id: str, message: str) -> None:
        self.messages.append(message)


class _FakeCommitSession:
    """最小同步 Session 替身：commit/add 均为空操作，不依赖真实 DB。"""

    def commit(self) -> None:
        pass

    def add(self, item: Any) -> None:
        pass


def test_process_youtube_progress_envelope_has_task_progress_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    capture = _CaptureSyncPublish()
    monkeypatch.setattr(process_youtube, "publish_task_update_sync", capture)

    task = _task()
    session = _FakeCommitSession()

    # _update_task 是同步函数，可直接调用（无需 asyncio）
    process_youtube._update_task(session, task, "transcribing", 50, "transcribing", None)

    assert capture.messages, "expected at least one published progress message"
    for raw in capture.messages:
        assert json.loads(raw)["kind"] == "task_progress"


def test_process_youtube_failure_envelope_has_task_progress_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    from app.core.exceptions import BusinessError
    from app.i18n.codes import ErrorCode

    capture = _CaptureSyncPublish()
    monkeypatch.setattr(process_youtube, "publish_task_update_sync", capture)
    monkeypatch.setattr(process_youtube.NotificationService, "notify", staticmethod(lambda *a, **k: None))

    task = _task()
    session = _FakeCommitSession()
    error = BusinessError(ErrorCode.ASR_SERVICE_FAILED)

    process_youtube._mark_failed(session, task, error, None)

    assert task.status == "failed"
    assert capture.messages, "expected a failure progress message"
    assert json.loads(capture.messages[-1])["kind"] == "task_progress"


def test_process_youtube_completed_calls_notify_task_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """YouTube 任务完成改调 NotificationService.notify(TASK_COMPLETED)。"""
    from app.services.notifications.types import NotificationType

    task = _task()  # duration_seconds=120.0, title="demo"
    calls: list[dict[str, Any]] = []

    def _spy_notify(sess: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(process_youtube.NotificationService, "notify", staticmethod(_spy_notify))
    monkeypatch.setattr(process_youtube, "publish_task_update_sync", lambda *a, **k: None)

    class _Sess:
        def commit(self) -> None:
            return None

    process_youtube._update_task(_Sess(), task, "completed", 100, "completed", "req-1")

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["type"] == NotificationType.TASK_COMPLETED
    assert kwargs["user_id"] == str(task.user_id)
    assert kwargs["task_id"] == str(task.id)
    assert kwargs["params"]["task_title"] == "demo"
    assert kwargs["params"]["duration"] == 120.0


def test_process_youtube_failed_calls_notify_task_failed_without_raw_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """YouTube 失败改调 notify(TASK_FAILED)，params 只带 error_code，不带原始错误。"""
    from app.core.exceptions import BusinessError
    from app.i18n.codes import ErrorCode
    from app.services.notifications.types import NotificationType

    task = _task()
    error = BusinessError(ErrorCode.ASR_SERVICE_FAILED, reason="leak this internal trace")
    calls: list[dict[str, Any]] = []

    def _spy_notify(sess: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(process_youtube.NotificationService, "notify", staticmethod(_spy_notify))
    monkeypatch.setattr(process_youtube, "publish_task_update_sync", lambda *a, **k: None)

    class _Sess:
        def commit(self) -> None:
            return None

    process_youtube._mark_failed(_Sess(), task, error, "req-1")

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["type"] == NotificationType.TASK_FAILED
    assert kwargs["task_id"] == str(task.id)
    assert kwargs["params"]["error_code"] == ErrorCode.ASR_SERVICE_FAILED.value
    for value in kwargs["params"].values():
        assert "leak this internal trace" not in str(value)


# ============================================================
# yt-dlp 抓取韧性：超时注入 + 瞬时/永久错误分类 + 仅瞬时重试
# ============================================================


class _FakeYDL:
    """模拟 yt-dlp 的 with 上下文；extract_info 依次消费 side_effects（异常即抛、否则返回）。"""

    _side_effects: list[Any] = []
    _calls: dict[str, int] = {"n": 0}

    def __init__(self, opts: dict[str, Any]) -> None:
        self.opts = opts

    def __enter__(self) -> _FakeYDL:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> Any:
        i = _FakeYDL._calls["n"]
        _FakeYDL._calls["n"] += 1
        effect = _FakeYDL._side_effects[i]
        if isinstance(effect, Exception):
            raise effect
        return effect

    def prepare_filename(self, info: Any) -> str:
        return "/tmp/fake.mp4"  # noqa: S108 - 测试桩


@pytest.mark.parametrize(
    "message,expected_transient",
    [
        ("Read timed out.", True),
        ("Connection reset by peer", True),
        ("HTTP Error 503: Service Unavailable", True),
        ("Unable to download webpage: <urlopen error timed out>", True),
        ("Private video. Sign in if you've been granted access", False),
        ("Video unavailable", False),
        ("This video has been removed by the uploader", False),
        ("Incomplete YouTube ID", False),
        ("Video is not available in your country", False),
    ],
)
def test_is_transient_youtube_error(message: str, expected_transient: bool) -> None:
    assert process_youtube._is_transient_youtube_error(Exception(message)) is expected_transient


def test_is_transient_false_for_business_error() -> None:
    # 已被归类的 BusinessError（含我们判定的永久失败）不再重试。
    from app.core.exceptions import BusinessError
    from app.i18n.codes import ErrorCode

    assert process_youtube._is_transient_youtube_error(BusinessError(ErrorCode.YOUTUBE_VIDEO_UNAVAILABLE)) is False


def test_classify_youtube_error_maps_known_keywords() -> None:
    from app.core.exceptions import BusinessError
    from app.i18n.codes import ErrorCode

    assert (
        process_youtube._classify_youtube_error(Exception("Private video")).code == ErrorCode.YOUTUBE_VIDEO_UNAVAILABLE
    )
    assert (
        process_youtube._classify_youtube_error(Exception("Video unavailable")).code
        == ErrorCode.YOUTUBE_VIDEO_UNAVAILABLE
    )
    assert (
        process_youtube._classify_youtube_error(Exception("Incomplete YouTube ID")).code == ErrorCode.INVALID_URL_FORMAT
    )
    assert (
        process_youtube._classify_youtube_error(Exception("Read timed out")).code == ErrorCode.YOUTUBE_DOWNLOAD_FAILED
    )
    # 已是 BusinessError 时原样返回（不二次包装）。
    be = BusinessError(ErrorCode.YOUTUBE_VIDEO_UNAVAILABLE)
    assert process_youtube._classify_youtube_error(be) is be


def test_run_with_youtube_retry_retries_transient_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_youtube.time, "sleep", lambda *_a: None)
    calls = {"n": 0}

    def _always_transient() -> None:
        calls["n"] += 1
        raise ValueError("read timed out")

    with pytest.raises(ValueError):
        process_youtube._run_with_youtube_retry(_always_transient, max_attempts=3, what="resolve")
    assert calls["n"] == 3  # 首次 + 2 次重试


def test_run_with_youtube_retry_no_retry_on_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_youtube.time, "sleep", lambda *_a: None)
    calls = {"n": 0}

    def _permanent() -> None:
        calls["n"] += 1
        raise ValueError("Private video")

    with pytest.raises(ValueError):
        process_youtube._run_with_youtube_retry(_permanent, max_attempts=3, what="resolve")
    assert calls["n"] == 1  # 永久错误立即抛出、不重试


def test_run_with_youtube_retry_returns_after_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_youtube.time, "sleep", lambda *_a: None)
    calls = {"n": 0}

    def _flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("connection reset by peer")
        return "ok"

    assert process_youtube._run_with_youtube_retry(_flaky, max_attempts=3, what="download") == "ok"
    assert calls["n"] == 3


def test_youtube_ydl_opts_injects_resilience(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(process_youtube.settings, "YOUTUBE_DOWNLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(process_youtube.settings, "YOUTUBE_OUTPUT_TEMPLATE", "%(id)s.%(ext)s")
    monkeypatch.setattr(process_youtube.settings, "YOUTUBE_DOWNLOAD_FORMAT", "bestaudio/best")
    monkeypatch.setattr(process_youtube.settings, "YOUTUBE_SOCKET_TIMEOUT", 42)
    monkeypatch.setattr(process_youtube.settings, "YOUTUBE_DOWNLOAD_RETRIES", 7)

    opts = process_youtube._youtube_ydl_opts()
    assert opts["socket_timeout"] == 42
    assert opts["retries"] == 7
    assert opts["fragment_retries"] == 7
    assert opts["extractor_retries"] == 7
    assert opts["noplaylist"] is True
    assert opts["format"] == "bestaudio/best"


def test_extract_youtube_info_retries_transient_then_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_youtube.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(process_youtube.settings, "YOUTUBE_RESOLVE_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(process_youtube, "_youtube_ydl_opts", lambda: {})
    _FakeYDL._calls["n"] = 0
    _FakeYDL._side_effects = [
        ValueError("read timed out"),
        {"title": "Hello", "url": "https://cdn.example/direct.m4a", "duration": 123},
    ]
    monkeypatch.setattr(process_youtube, "YoutubeDL", _FakeYDL)

    direct_url, title, duration = process_youtube._extract_youtube_info("https://youtu.be/abc")
    assert title == "Hello"
    assert direct_url == "https://cdn.example/direct.m4a"
    assert duration == 123
    assert _FakeYDL._calls["n"] == 2  # 1 次瞬时失败 + 1 次成功


# ============================================================
# 溯源:YouTube 摄入路径与 process_audio 同口径地写回 ASR 与摘要溯源。
# 此前 process_youtube 自带平行实现,加溯源时只改了共享/audio 侧,漏了 youtube ——
# 实测 YouTube 任务 asr_variant/asr_engine 恒 NULL、summary 的 slug/version/token 全 NULL。
# ============================================================


class _EngineAsrSvc:
    """有引擎概念的 ASR provider(如 tencent),engine_for_variant 把变体映射到具体引擎。"""

    provider = "tencent"

    def engine_for_variant(self, variant: str) -> str:
        return "16k_zh_fast" if variant == "file_fast" else "16k_zh"


class _EnginelessAsrSvc:
    """无引擎概念的 ASR provider(如 aliyun/volcengine):没有 engine_for_variant。"""

    provider = "aliyun"


def test_apply_asr_provenance_records_provider_variant_engine() -> None:
    task = _task()
    task.options = {}
    process_youtube._apply_asr_provenance(task, _EngineAsrSvc(), "tencent", "file_fast")
    assert task.asr_provider == "tencent"
    assert task.asr_variant == "file_fast"
    assert task.asr_engine == "16k_zh_fast"  # engine_for_variant 同源记录实际引擎
    assert task.options["asr_variant"] == "file_fast"


def test_apply_asr_provenance_engineless_provider_sets_variant_keeps_engine_none() -> None:
    """这正是线上 bug 的实锤:无引擎 provider 也必须写回 asr_variant(归一后的 file),
    engine 列因 provider 无引擎概念留 NULL —— 而旧 youtube 路径连 asr_variant 都不写。"""
    task = _task()
    task.options = {}
    process_youtube._apply_asr_provenance(task, _EnginelessAsrSvc(), "aliyun", "file")
    assert task.asr_provider == "aliyun"
    assert task.asr_variant == "file"  # 旧路径恒 None,这里必须是 "file"
    assert task.asr_engine is None  # aliyun 无引擎概念,列留 NULL(与 audio 路径同口径)


async def test_summarize_one_carries_prompt_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """YouTube 摘要改走共享 _generate_single_summary,Summary 必须带 slug/version/真实 token。"""

    async def _fake_generate_single_summary(
        *, text: str, summary_type: str, content_style: str, quality_notice: str, llm_service: Any
    ) -> tuple[str, dict[str, Any]]:
        return "生成的摘要正文内容", {
            "slug": f"summary-{summary_type}-zh",
            "version": "1.9.0",
            "input_tokens": 12,
            "output_tokens": 34,
        }

    monkeypatch.setattr(process_youtube, "_generate_single_summary", _fake_generate_single_summary)

    class _LLM:
        provider = "proxy"
        model_name = "deepseek-reasoner"

    summary = await process_youtube._summarize_one(
        task=_task(),
        summary_type="overview",
        full_text="转写文本",
        content_style="meeting",
        llm_service=_LLM(),
    )
    assert summary.summary_type == "overview"
    assert summary.content == "生成的摘要正文内容"
    assert summary.model_used == "deepseek-reasoner"
    assert summary.prompt_slug == "summary-overview-zh"
    assert summary.prompt_version == "1.9.0"
    assert summary.input_tokens == 12
    assert summary.output_tokens == 34
    assert summary.token_count == 34  # 真实 output token 优先于字符数近似


# ============================================================
# Defect #1 回归：source_type="url" 任务不被 guard 短路
# ============================================================


class _SentinelError(Exception):
    """哨兵异常：_extract_youtube_info 被调用时抛出，用于证明 guard 已被越过。"""


class _FakeContextSession:
    """get_sync_db_session() 上下文管理器的最小替身，不需要真实 DB。"""

    def __init__(self, task: Task) -> None:
        self._task = task

    def __enter__(self) -> _FakeContextSession:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def query(self, model: Any) -> _FakeSyncQuery:
        return _FakeSyncQuery([self._task])

    def commit(self) -> None:
        pass

    def add(self, item: Any) -> None:
        pass


def test_process_youtube_url_source_type_passes_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """source_type="url" 任务必须越过 guard，执行到 _extract_youtube_info 阶段。

    测试策略：
    - 用 source_type="url" 的 Task 替身替换 _get_task；
    - 将 _extract_youtube_info 替换为抛出 _SentinelError 的桩，
      证明 guard 已被越过（若 guard 短路，_SentinelError 永远不会被抛出）；
    - 捕获 _SentinelError 作为断言证据。

    此测试在 OLD guard（!= "youtube"）下必定失败（_SentinelError 不被抛出，
    而是 _process_youtube 静默 return；pytest.raises 检测到无异常则报 Failed）。
    """
    url_task = Task(
        user_id="user-url",
        content_hash="hash-url",
        title="bilibili-demo",
        source_type="url",
        source_url="https://www.bilibili.com/video/BV1xx",
        duration_seconds=None,
    )
    # source_key=None 确保不会走 skip_download 分支（无法在 guard 之后短路前就退出）
    url_task.source_key = None  # type: ignore[attr-defined]

    fake_session = _FakeContextSession(url_task)

    # 替换上下文管理器工厂，让每次 `with get_sync_db_session()` 都返回同一 fake_session
    from collections.abc import Generator
    from contextlib import contextmanager

    @contextmanager
    def _fake_get_sync_db_session() -> Generator[_FakeContextSession, None, None]:
        yield fake_session

    monkeypatch.setattr(process_youtube, "get_sync_db_session", _fake_get_sync_db_session)

    # _get_task 从 session 中取任务，用真实 _get_task 会查 DB；直接打桩返回 url_task。
    monkeypatch.setattr(process_youtube, "_get_task", lambda session, task_id: url_task)

    # validate_ingest_url 需要白名单，直接 noop 跳过
    monkeypatch.setattr(process_youtube.TaskService, "validate_ingest_url", staticmethod(lambda url: None))

    # StageManager 会调用 DB，打桩为 noop
    class _NoopStageManager:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def skip_stage(self, *args: Any, **kwargs: Any) -> None:
            pass

        def start_stage(self, *args: Any, **kwargs: Any) -> None:
            pass

        def complete_stage(self, *args: Any, **kwargs: Any) -> None:
            pass

        def fail_stage(self, *args: Any, **kwargs: Any) -> None:
            pass

    monkeypatch.setattr(process_youtube, "StageManager", _NoopStageManager)

    # _mark_failed 写 DB + 通知，打桩为 noop（_SentinelError 进入 except 分支会调用它）
    monkeypatch.setattr(process_youtube, "_mark_failed", lambda *a, **k: None)

    # publish_task_update_sync 写 Redis，打桩为 noop 避免连接错误
    monkeypatch.setattr(process_youtube, "publish_task_update_sync", lambda *a, **k: None)

    # 哨兵计数器：guard 被越过后，执行会到达 _extract_youtube_info；
    # 若 guard 短路（旧代码），extract_calls["n"] 永远是 0，断言失败。
    # _extract_youtube_info 的返回值被后续代码使用，
    # 但其 except 分支会 return，所以返回任意值让 _process_youtube 正常退出即可。
    extract_calls: dict[str, int] = {"n": 0}

    def _counting_extract(url: str) -> tuple[str | None, str | None, int | None]:
        extract_calls["n"] += 1
        # 返回 None 值触发 _duration_over_cap(None) → False，然后进 complete_stage；
        # 但 complete_stage 已被 _NoopStageManager 打桩，流程会继续但最终在 _update_metadata
        # 等地方遇到桩失败；不过我们只关心 extract_calls["n"] > 0。
        # 抛出 ValueError 让 _process_youtube 的 except 分支 return，干净退出：
        raise ValueError("sentinel: extract called, exiting cleanly")

    monkeypatch.setattr(process_youtube, "_extract_youtube_info", _counting_extract)

    # 运行（内部捕获 ValueError，调用 _mark_failed(已打桩为 noop)，然后 return）
    process_youtube._process_youtube(str(url_task.id), "req-url-1")

    # 关键断言：若 guard 短路（旧代码 != "youtube"），n 恒为 0
    assert extract_calls["n"] == 1, (
        "guard short-circuited for source_type='url': _extract_youtube_info was never called"
    )
