from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from app.i18n.codes import ErrorCode

_LOCALE_FILES: Dict[str, Path] = {
    "zh": Path(__file__).resolve().parents[1] / "i18n" / "zh.json",
    "en": Path(__file__).resolve().parents[1] / "i18n" / "en.json",
}
_CACHE: Dict[str, Dict[int, str]] = {}


def _load_locale_messages(locale: str) -> Dict[int, str]:
    if locale in _CACHE:
        return _CACHE[locale]
    path = _LOCALE_FILES.get(locale)
    if path is None or not path.exists():
        _CACHE[locale] = {}
        return _CACHE[locale]
    raw_text = path.read_text(encoding="utf-8")
    data: object = json.loads(raw_text)
    if not isinstance(data, dict):
        _CACHE[locale] = {}
        return _CACHE[locale]
    normalized: Dict[int, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if not key.isdigit():
            continue
        normalized[int(key)] = value
    _CACHE[locale] = normalized
    return normalized


def get_message(code: ErrorCode, locale: str, **kwargs: str) -> str:
    messages = _load_locale_messages(locale)
    template = messages.get(code.value)
    if template is None:
        fallback = _load_locale_messages("zh")
        template = fallback.get(code.value, "未知错误")
    if not kwargs:
        return template
    try:
        return template.format_map(kwargs)
    except KeyError:
        return template
