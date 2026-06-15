from __future__ import annotations

from app.services.media_url import build_presigned_media_url

# 公开配图直链 TTL(秒):媒体字节直连 OSS,绕开同源代理经 cloudflared 隧道的 ~1.5s/请求基线。
# 安全面:公开端点每次请求都过 is_public DB 复核(取消公开后新请求拿不到新签名),
# 已签出 URL 的残余暴露 ≤TTL——与既有音频 307 预签名(app/api/v1/media.py)同类且被接受。
PUBLIC_IMAGE_PRESIGN_EXPIRES = 600

# 存量配图 URL 的同源代理前缀(worker/tasks/image_generator.py 写库格式:
# /api/v1/summaries/images/{user_id}/{task_id}/{image_id}.{fmt}),
# OSS 对象 key = "summary_images/" + 前缀后的路径(见 app/api/v1/summaries.py 图片端点)。
SUMMARY_IMAGE_PROXY_PREFIX = "/api/v1/summaries/images/"


async def public_summary_image_url(raw_url: object, status: str) -> str | None:
    """ready 配图换发短 TTL OSS 直链;非 ready/形态不识别/签发失败一律回落存量代理 URL。"""
    if not isinstance(raw_url, str) or not raw_url:
        return None
    if status != "ready" or not raw_url.startswith(SUMMARY_IMAGE_PROXY_PREFIX):
        return raw_url
    object_key = f"summary_images/{raw_url[len(SUMMARY_IMAGE_PROXY_PREFIX) :]}"
    return await build_presigned_media_url(object_key, PUBLIC_IMAGE_PRESIGN_EXPIRES) or raw_url


def first_ready_image_url(images: list[dict[str, object]] | None) -> str | None:
    """图集中首个 status==ready 且 url 非空的原始(代理)URL;无则 None。

    不取 images[0]:pending/failed 槽可能排在 ready 之前(末张永远 pending 的历史形态)。
    """
    if not images:
        return None
    for item in images:
        if not isinstance(item, dict):
            continue
        if item.get("status") == "ready":
            url = item.get("url")
            if isinstance(url, str) and url:
                return url
    return None
