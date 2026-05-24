"""image-service 生图 Provider

通过部署在 dev 服务器上的独立 image-service（FastAPI + LiteLLM Proxy + Gemini）
完成图像生成。该服务自带 Redis 缓存、限流、防雪崩锁与重试，
本 provider 只需做：调用 /v1/generate → 下载静态图片 → 返回 bytes。

本类继承 LLMService 是为了能被 SmartFactory 统一管理（缓存、健康检查），
但只实现 generate_image()；其余 LLM 文本能力均不支持。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import settings
from app.core.exceptions import BusinessError
from app.core.fault_tolerance import CircuitBreaker, CircuitBreakerConfig, RetryConfig, retry
from app.core.monitoring import monitor
from app.core.registry import ServiceMetadata, register_service
from app.i18n.codes import ErrorCode
from app.services.llm.base import LLMService

logger = logging.getLogger(__name__)

# image-service 单次生图最长允许 5 分钟（与上游一致）
_REQUEST_TIMEOUT = 300.0
# 静态图片下载超时
_DOWNLOAD_TIMEOUT = 60.0
# image-service 单边最大像素
_MAX_DIMENSION = 2048


def _resolve_size(aspect_ratio: str, image_size: str | None) -> str:
    """根据 aspect_ratio 和模糊尺寸标记推导 image-service 需要的 'WxH'

    - image_size = "1K"/"2K"/"4K" → 长边像素 1024/2048/2048（4K 截断到 2K 上限）
    - 默认 2K
    - aspect_ratio 形如 "16:9"；不合法时回退为 1:1
    """
    longest = 2048
    if image_size:
        token = image_size.strip().upper()
        if token == "1K":
            longest = 1024
        elif token == "2K":
            longest = 2048
        elif token == "4K":
            longest = _MAX_DIMENSION
        elif "X" in token:
            # 已经是 WxH，直接使用（但仍要 clamp）
            try:
                w, h = (int(v) for v in token.split("X"))
                w = min(max(w, 1), _MAX_DIMENSION)
                h = min(max(h, 1), _MAX_DIMENSION)
                return f"{w}x{h}"
            except ValueError:
                pass

    longest = min(longest, _MAX_DIMENSION)

    try:
        a, b = (int(v) for v in aspect_ratio.split(":"))
        if a <= 0 or b <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        a, b = 1, 1

    if a >= b:
        width = longest
        height = max(1, round(longest * b / a))
    else:
        height = longest
        width = max(1, round(longest * a / b))

    width = min(width, _MAX_DIMENSION)
    height = min(height, _MAX_DIMENSION)
    return f"{width}x{height}"


@register_service(
    "llm",
    "image_service",
    metadata=ServiceMetadata(
        name="image_service",
        service_type="llm",
        priority=10,
        description="远程 image-service（Gemini 系图像生成）",
        display_name="Image Service (Gemini)",
        cost_per_million_tokens=0.0,
        rate_limit=20,
    ),
)
class ImageServiceLLMService(LLMService):
    """通过 image-service 调用 Gemini 系图像生成模型"""

    _circuit_breaker = CircuitBreaker.get_or_create(
        "image_service",
        CircuitBreakerConfig(
            failure_threshold=5,
            success_threshold=2,
            timeout=60.0,
            expected_exception=(BusinessError, httpx.HTTPError),
        ),
    )

    def __init__(self, config: object | None = None, model_id: str | None = None) -> None:
        from app.services.config_utils import get_config_value

        base_url = get_config_value(config, "base_url", settings.IMAGE_SERVICE_BASE_URL)
        api_key = get_config_value(config, "api_key", settings.IMAGE_SERVICE_API_KEY)
        default_model = get_config_value(
            config,
            "default_model",
            settings.IMAGE_SERVICE_DEFAULT_MODEL,
        )

        if not base_url:
            raise RuntimeError("IMAGE_SERVICE_BASE_URL is not set")

        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model_id or default_model

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "image_service"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    @retry(
        RetryConfig(max_attempts=2, initial_delay=1.0, max_delay=5.0),
        exceptions=(httpx.TimeoutException, httpx.NetworkError),
    )
    @monitor("llm", "image_service")
    async def generate_image(
        self,
        prompt: str,
        aspect_ratio: str = "1:1",
        image_size: str | None = "2K",
        style: str | None = None,
        **_: Any,
    ) -> bytes:
        """调用 image-service 生图并下载图片 bytes。

        额外的 ``system_message`` / ``temperature`` / ``max_tokens`` 等参数
        被忽略（image-service 不支持），保证与既有 OpenRouter 调用签名兼容。
        """
        if not prompt:
            raise BusinessError(ErrorCode.INVALID_PARAMETER, detail="prompt")

        # image-service 的 model 字段只接受裸 model id，例如 "gemini-3-pro-image-preview"
        # 兼容遗留配置形如 "google/gemini-3-pro-image-preview"
        model = self._model.split("/", 1)[-1] if self._model else None

        payload: dict[str, Any] = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "size": _resolve_size(aspect_ratio, image_size),
        }
        if model:
            payload["model"] = model
        if style:
            payload["style"] = style

        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=_REQUEST_TIMEOUT) as client:
                resp = await client.post("/v1/generate", json=payload, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"image-service request timeout: {exc}",
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            body = exc.response.text[:500]
            if status == 429:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"image-service rate limited (HTTP {status}): {body}",
                ) from exc
            if 500 <= status < 600:
                raise BusinessError(
                    ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                    reason=f"image-service upstream error (HTTP {status}): {body}",
                ) from exc
            raise BusinessError(
                ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                reason=f"image-service request failed (HTTP {status}): {body}",
            ) from exc
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"image-service network error: {exc}",
            ) from exc

        image_url = data.get("image_url")
        if not image_url:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_GENERATION_FAILED,
                reason=f"image-service returned no image_url: {data}",
            )

        # image-service 返回的可能是相对路径（如 "/static/images/abc.png"），需要拼上 base_url
        download_url = image_url if image_url.startswith("http") else f"{self._base_url}{image_url}"

        try:
            async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT) as client:
                img_resp = await client.get(download_url)
                img_resp.raise_for_status()
                return img_resp.content
        except httpx.HTTPError as exc:
            raise BusinessError(
                ErrorCode.AI_SUMMARY_SERVICE_UNAVAILABLE,
                reason=f"image-service download failed ({download_url}): {exc}",
            ) from exc

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=10.0) as client:
                resp = await client.get("/v1/health")
                if resp.status_code != 200:
                    return False
                body = resp.json()
                return body.get("status") in {"healthy", "degraded"}
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 以下方法仅为满足 LLMService 抽象接口；本 provider 仅支持生图。
    # ------------------------------------------------------------------

    async def summarize(self, text: str, summary_type: str, content_style: str = "meeting") -> str:
        raise NotImplementedError("image_service provider only supports generate_image()")

    async def summarize_stream(
        self, text: str, summary_type: str, content_style: str = "meeting"
    ) -> AsyncIterator[str]:
        raise NotImplementedError("image_service provider only supports generate_image()")
        yield  # pragma: no cover -- make it a generator

    async def generate(
        self,
        prompt: str,
        system_message: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        raise NotImplementedError("image_service provider only supports generate_image()")

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        raise NotImplementedError("image_service provider only supports generate_image()")

    async def chat_stream(self, messages: list[dict[str, str]], **kwargs: Any) -> AsyncIterator[str]:
        raise NotImplementedError("image_service provider only supports generate_image()")
        yield  # pragma: no cover

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return 0.0
