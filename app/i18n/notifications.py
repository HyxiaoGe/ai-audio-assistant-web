"""通知文案目录（后端服务端渲染 + InAppChannel 的 zh 兜底串）。

设计：
- 通知行只存 type + 语言无关 params；in-app 主渲染在前端 t() 做边缘渲染。
- 服务端渠道（飞书）与 InAppChannel 写库时的 title/message 兜底，都走这里。
- task_failed 的正文按 error_code 选友好本地化文案；未映射的 code 回落通用友好语，
  绝不把原始内部错误（params 里的 error/堆栈）透给用户。
- 渲染容错对齐 app/core/i18n.py：format_map 缺键不抛，安全返回模板本体。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DEFAULT_LOCALE = "zh"

# i18n_key -> locale -> {"title": tmpl, "body": tmpl}
NOTIFICATION_TEXT: dict[str, dict[str, dict[str, str]]] = {
    "notif.task_completed": {
        "zh": {"title": "任务已完成", "body": "《{task_title}》已处理完成，点击查看结果。"},
        "en": {"title": "Task completed", "body": "“{task_title}” is done. Tap to view the result."},
    },
    "notif.task_failed": {
        "zh": {"title": "任务处理失败", "body": "《{task_title}》处理失败，请稍后重试。"},
        "en": {"title": "Task failed", "body": "“{task_title}” failed to process. Please try again later."},
    },
    "notif.quota_alert": {
        "zh": {
            "title": "ASR 配额提醒",
            "body": "{provider} 的语音识别配额已使用 {threshold}%，请注意用量。",
        },
        "en": {
            "title": "ASR quota alert",
            "body": "Your {provider} ASR quota has reached {threshold}%. Please mind your usage.",
        },
    },
    "notif.youtube_reauth_required": {
        "zh": {"title": "YouTube 需要重新授权", "body": "YouTube 授权已失效，请重新连接以继续同步。"},
        "en": {
            "title": "YouTube re-authorization required",
            "body": "Your YouTube authorization has expired. Please reconnect to keep syncing.",
        },
    },
    "notif.visual_failed": {
        "zh": {"title": "图示生成失败", "body": "《{task_title}》的可视化图示生成失败，文字摘要不受影响。"},
        "en": {
            "title": "Visual generation failed",
            "body": "Visual diagrams for “{task_title}” could not be generated. Your text summary is unaffected.",
        },
    },
}

# task_failed 正文按 error_code 选友好文案（值为 ErrorCode.value，避免 import 循环写裸 int + 注释）。
_TASK_FAILED_BODY_BY_ERROR_CODE: dict[int, dict[str, str]] = {
    51002: {  # ASR_SERVICE_FAILED
        "zh": "《{task_title}》语音识别失败，请稍后重试。",
        "en": "Speech recognition for “{task_title}” failed. Please try again later.",
    },
    51102: {  # AI_SUMMARY_GENERATION_FAILED
        "zh": "《{task_title}》摘要生成失败，请稍后重试。",
        "en": "Summary generation for “{task_title}” failed. Please try again later.",
    },
    51300: {  # YOUTUBE_DOWNLOAD_FAILED
        "zh": "《{task_title}》下载失败，请检查链接后重试。",
        "en": "Downloading “{task_title}” failed. Please check the link and try again.",
    },
    40011: {  # FILE_TOO_LARGE
        "zh": "《{task_title}》文件过大，处理失败，请压缩后重试。",
        "en": "“{task_title}” is too large to process. Please compress it and try again.",
    },
    40018: {  # CHANNEL_BLOCKED
        "zh": "《{task_title}》所在频道已被屏蔽，无法转写。",
        "en": "“{task_title}” belongs to a blocked channel and cannot be transcribed.",
    },
}

# 任何兜底场景下的通用友好串（绝不露原始错误）。
_GENERIC_FALLBACK: dict[str, dict[str, str]] = {
    "zh": {"title": "通知", "body": "您有一条新通知。"},
    "en": {"title": "Notification", "body": "You have a new notification."},
}


def _safe_format(template: str, params: dict) -> str:
    """format_map 容错：缺键 / 类型问题都不抛，安全返回模板本体（对齐 core/i18n.py）。"""
    try:
        return template.format_map(params)
    except (KeyError, IndexError, ValueError):
        return template


def render_notification(i18n_key: str, params: dict, locale: str) -> tuple[str, str]:
    """渲染通知 (title, body)。永不抛；未知 key / locale / 缺 param 安全降级。

    Args:
        i18n_key: 形如 "notif.task_completed"
        params: 语言无关渲染参数（task_failed 走 params["error_code"] 选正文）
        locale: "zh" | "en"，未知回落 zh

    Returns:
        (title, body) 二元组，均为已渲染字符串。
    """
    loc = locale if locale in (_DEFAULT_LOCALE, "en") else _DEFAULT_LOCALE
    entry = NOTIFICATION_TEXT.get(i18n_key)
    if entry is None:
        fb = _GENERIC_FALLBACK[loc]
        return fb["title"], fb["body"]

    # 假设 catalog 条目永不为空 dict（or 短路靠真值判断）：本表是模块级常量，扩充时须保证
    # 每个 locale 项含非空 title/body，否则会意外回落到默认语言。
    texts = entry.get(loc) or entry.get(_DEFAULT_LOCALE) or _GENERIC_FALLBACK[loc]
    title_tmpl = texts.get("title", _GENERIC_FALLBACK[loc]["title"])
    body_tmpl = texts.get("body", _GENERIC_FALLBACK[loc]["body"])

    # task_failed：按 error_code 覆盖正文为友好文案；未映射保持默认正文（不露原始错误）。
    if i18n_key == "notif.task_failed":
        error_code = params.get("error_code")
        mapped = _TASK_FAILED_BODY_BY_ERROR_CODE.get(error_code) if isinstance(error_code, int) else None
        if mapped is not None:
            body_tmpl = mapped.get(loc) or mapped.get(_DEFAULT_LOCALE, body_tmpl)

    return _safe_format(title_tmpl, params), _safe_format(body_tmpl, params)
