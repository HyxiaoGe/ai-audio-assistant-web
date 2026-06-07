from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest


def _load_image_generator_module():
    module_path = Path(__file__).resolve().parents[2] / "worker" / "tasks" / "image_generator.py"
    spec = importlib.util.spec_from_file_location("image_generator_under_test", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load image_generator module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


image_generator = _load_image_generator_module()


def test_auto_images_enabled_for_overview_does_not_depend_on_content_style(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        image_generator,
        "get_auto_images_config",
        lambda: {
            "enabled": True,
            "supported_summary_types": ["overview"],
            "supported_content_styles": ["meeting"],
        },
    )

    assert image_generator.is_auto_images_enabled("overview", "review") is True
    assert image_generator.is_auto_images_enabled("key_points", "review") is False


@pytest.mark.asyncio
async def test_process_summary_images_plans_default_article_image_when_summary_has_no_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        image_generator,
        "get_auto_images_config",
        lambda: {
            "enabled": True,
            "max_images": 3,
            "timeout_seconds": 60,
            "supported_summary_types": ["overview"],
        },
    )

    generated_placeholders: list[list[dict]] = []

    async def fake_generate_images_parallel(
        placeholders: list[dict],
        *_args: object,
        **_kwargs: object,
    ) -> list[dict]:
        generated_placeholders.append(placeholders)
        return [
            {
                "placeholder": placeholders[0]["placeholder"],
                "url": "/api/v1/summaries/images/user/task/image.png",
                "status": "success",
                "model_id": "image-model",
            }
        ]

    monkeypatch.setattr(image_generator, "generate_images_parallel", fake_generate_images_parallel)

    content = (
        "## 实测 Gemini 3.5 Flash 与 Omni Flash\n\n"
        "这段内容比较了多个 AI 产品的体验差异、优势和不足。\n\n"
        "整体结论是新模型在速度和多模态能力上更进一步。"
    )

    final_content, image_results = await image_generator.process_summary_images(
        content=content,
        task_id="task-1",
        user_id="user-1",
        summary_type="overview",
        content_style="review",
    )

    assert generated_placeholders
    placeholder = generated_placeholders[0][0]
    assert placeholder["type"] == "comparison"
    assert "Gemini 3.5 Flash 与 Omni Flash" in placeholder["description"]
    assert image_results[0]["status"] == "success"
    assert "![实测 Gemini 3.5 Flash 与 Omni Flash]" in final_content
    assert "这段内容比较了多个 AI 产品" in final_content


# ============================================================
# 有界并发生成（generate_images_parallel + Semaphore）相关
# ============================================================


def _img_placeholders(n: int) -> list[dict]:
    """构造 n 个可区分身份的占位符（P0..P{n-1}），结果据 placeholder 回填/断言。"""
    return [
        {"placeholder": f"P{i}", "type": "infographic", "description": f"d{i}", "key_texts": []}
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_generate_images_parallel_respects_concurrency_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """在途 generate_single_image 调用峰值不得超过 max_concurrency（证明信号量真生效）。"""
    state = {"current": 0, "peak": 0}

    async def fake_generate_single_image(item: dict, *_args: object, **_kwargs: object) -> dict:
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        try:
            # 持槽一会儿，让多张有机会重叠，从而暴露真实峰值。
            await asyncio.sleep(0.02)
        finally:
            state["current"] -= 1
        return {"placeholder": item["placeholder"], "url": "/img.png", "status": "success", "model_id": "m"}

    monkeypatch.setattr(image_generator, "generate_single_image", fake_generate_single_image)

    placeholders = _img_placeholders(5)  # 5 张
    results = await image_generator.generate_images_parallel(
        placeholders, "u", "t", max_images=5, max_concurrency=2
    )

    assert len(results) == 5
    assert state["peak"] <= 2  # 5 张但峰值被信号量压在 2
    assert state["peak"] >= 2  # 且确实并发起来了（非退化为串行）


@pytest.mark.asyncio
async def test_generate_images_parallel_preserves_order_despite_completion_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """完成顺序与输入顺序相反时，最终结果仍严格按输入顺序回填（final_results[index]）。"""

    async def fake_generate_single_image(item: dict, *_args: object, **_kwargs: object) -> dict:
        # 越靠前（P0）睡越久 → 越晚完成，制造与输入序相反的完成顺序。
        idx = int(item["placeholder"][1:])
        await asyncio.sleep(0.02 * (5 - idx))
        return {"placeholder": item["placeholder"], "url": f"/img{idx}.png", "status": "success", "model_id": "m"}

    monkeypatch.setattr(image_generator, "generate_single_image", fake_generate_single_image)

    placeholders = _img_placeholders(4)
    results = await image_generator.generate_images_parallel(
        placeholders, "u", "t", max_images=4, max_concurrency=3
    )

    assert [r["placeholder"] for r in results] == ["P0", "P1", "P2", "P3"]
    assert [r["url"] for r in results] == ["/img0.png", "/img1.png", "/img2.png", "/img3.png"]


@pytest.mark.asyncio
async def test_generate_images_parallel_single_failure_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单张失败（如 429 → generate_single_image 返回 failed）只占该槽位，不连累其它张、不抛出。"""

    async def fake_generate_single_image(item: dict, *_args: object, **_kwargs: object) -> dict:
        if item["placeholder"] == "P1":
            return {"placeholder": "P1", "url": None, "status": "failed", "error": "429", "model_id": "m"}
        return {"placeholder": item["placeholder"], "url": "/img.png", "status": "success", "model_id": "m"}

    monkeypatch.setattr(image_generator, "generate_single_image", fake_generate_single_image)

    placeholders = _img_placeholders(3)
    results = await image_generator.generate_images_parallel(
        placeholders, "u", "t", max_images=3, max_concurrency=2
    )

    by_ph = {r["placeholder"]: r for r in results}
    assert len(results) == 3
    assert by_ph["P1"]["status"] == "failed"
    assert by_ph["P0"]["status"] == "success"
    assert by_ph["P2"]["status"] == "success"
    # 顺序仍严格按输入
    assert [r["placeholder"] for r in results] == ["P0", "P1", "P2"]


@pytest.mark.asyncio
async def test_generate_images_parallel_invokes_callback_once_per_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """on_image_ready 每张恰好回调一次，completed 递增覆盖 1..total，total 恒为图片数。"""

    async def fake_generate_single_image(item: dict, *_args: object, **_kwargs: object) -> dict:
        return {"placeholder": item["placeholder"], "url": "/img.png", "status": "success", "model_id": "m"}

    monkeypatch.setattr(image_generator, "generate_single_image", fake_generate_single_image)

    calls: list[tuple[int, int]] = []

    def on_image_ready(result: dict, current: int, total: int) -> None:
        calls.append((current, total))

    placeholders = _img_placeholders(4)
    await image_generator.generate_images_parallel(
        placeholders, "u", "t", max_images=4, max_concurrency=2, on_image_ready=on_image_ready
    )

    assert len(calls) == 4
    assert sorted(c for c, _ in calls) == [1, 2, 3, 4]
    assert {t for _, t in calls} == {4}


def test_generate_images_parallel_runs_under_asyncio_run_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两个生图入口都经 asyncio.run（每次新 loop）。Semaphore 在函数体内新建，
    连续两次 asyncio.run 不得报 'bound to a different event loop'。"""

    async def fake_generate_single_image(item: dict, *_args: object, **_kwargs: object) -> dict:
        return {"placeholder": item["placeholder"], "url": "/img.png", "status": "success", "model_id": "m"}

    monkeypatch.setattr(image_generator, "generate_single_image", fake_generate_single_image)

    placeholders = _img_placeholders(3)

    first = asyncio.run(
        image_generator.generate_images_parallel(placeholders, "u", "t", max_images=3, max_concurrency=2)
    )
    second = asyncio.run(
        image_generator.generate_images_parallel(placeholders, "u", "t", max_images=3, max_concurrency=2)
    )

    assert [r["placeholder"] for r in first] == ["P0", "P1", "P2"]
    assert [r["placeholder"] for r in second] == ["P0", "P1", "P2"]


# ============================================================
# WebP 压缩（_encode_webp）相关
# ============================================================


def test_encode_webp_produces_valid_webp() -> None:
    """png 字节经 _encode_webp 应得到合法 WebP（RIFF....WEBP 头）+ 格式后缀 webp。"""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (64, 64), (120, 200, 64)).save(buf, format="PNG")

    data, fmt = image_generator._encode_webp(buf.getvalue())

    assert fmt == "webp"
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def test_encode_webp_falls_back_to_png_on_invalid_input() -> None:
    """非图片字节无法解码时回退原始数据 + 格式 png（绝不丢图、不抛出）。"""
    data, fmt = image_generator._encode_webp(b"not-an-image")

    assert fmt == "png"
    assert data == b"not-an-image"


def test_dedupe_key_texts_preserves_first_occurrence_order() -> None:
    """重复标签按首次出现顺序去重，非重复项原样保留。"""
    assert image_generator._dedupe_key_texts(["A", "B", "A", "C", "B"]) == ["A", "B", "C"]
    assert image_generator._dedupe_key_texts(["X", "Y", "Z"]) == ["X", "Y", "Z"]
    assert image_generator._dedupe_key_texts([]) == []


def test_extract_image_placeholders_dedupes_duplicate_key_texts() -> None:
    """占位符里重复的 key_texts 在解析阶段即去重，避免配图把同一文字渲染多次。"""
    content = "{{IMAGE: infographic | 三大挑战 | 挑战1, 挑战2, 挑战2, 挑战3}}"
    specs = image_generator.extract_image_placeholders(content)

    assert len(specs) == 1
    assert specs[0]["key_texts"] == ["挑战1", "挑战2", "挑战3"]


def test_extract_image_placeholders_dedupes_single_brace_format() -> None:
    """单花括号新格式同样去重（与双花括号同语义）。"""
    content = "{IMAGE: timeline | 历程 | 2003年, 2006年, 2006年, 2018年}"
    specs = image_generator.extract_image_placeholders(content)

    assert len(specs) == 1
    assert specs[0]["key_texts"] == ["2003年", "2006年", "2018年"]
