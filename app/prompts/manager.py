"""提示词管理器"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.exceptions import BusinessError
from app.i18n.codes import ErrorCode


class PromptManager:
    """提示词管理器"""

    _instance: Optional[PromptManager] = None
    _prompts_cache: Dict[str, Dict] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "_initialized"):
            self.prompts_dir = Path(__file__).parent / "templates"
            self._initialized = True

    def get_prompt(
        self,
        category: str,
        prompt_type: str,
        locale: str = "zh-CN",
        variables: Optional[Dict[str, Any]] = None,
        content_style: Optional[str] = None,
    ) -> Dict[str, Any]:
        """获取提示词

        Args:
            category: 类别（如 summary, segmentation）
            prompt_type: 提示词类型（如 overview, key_points, action_items, segment）
            locale: 语言（如 zh-CN, en-US）
            variables: 模板变量（如 {transcript}, {quality_notice}）
            content_style: 内容风格（如 meeting, lecture, podcast）- 可选，也可在variables中提供

        Returns:
            包含 system, user_prompt, model_params 的字典
        """
        prompt_data = self._load_prompts(category, locale)
        config_data = self._load_config(category)

        if prompt_type not in prompt_data["prompts"]:
            raise BusinessError(
                ErrorCode.INVALID_PARAMETER, detail=f"Unknown prompt type: {prompt_type}"
            )

        # 获取content_style（优先使用参数，其次从variables中获取，最后默认为meeting）
        if content_style is None:
            content_style = variables.get("content_style", "meeting") if variables else "meeting"

        # 获取prompt模板（支持新旧两种格式）
        prompt_config = prompt_data["prompts"][prompt_type]

        # 新格式：templates字典（针对不同content_style）
        if "templates" in prompt_config:
            templates = prompt_config["templates"]
            if content_style in templates:
                prompt_template = templates[content_style]
            else:
                # 如果没有指定风格的模板，使用meeting或第一个可用的
                prompt_template = templates.get("meeting", list(templates.values())[0])
        # 旧格式：单个template字符串
        elif "template" in prompt_config:
            prompt_template = prompt_config["template"]
        else:
            raise BusinessError(
                ErrorCode.SYSTEM_ERROR,
                reason=f"No template found for {category}/{prompt_type}",
            )

        # 替换模板变量
        if variables:
            try:
                user_prompt = prompt_template.format(**variables)
            except KeyError as e:
                # 缺少必需的变量，提供更友好的错误信息
                raise BusinessError(
                    ErrorCode.INVALID_PARAMETER,
                    reason=f"Missing required variable in prompt template: {e}",
                )
        else:
            user_prompt = prompt_template

        # 获取system配置
        system_config = prompt_data["system"]

        # 根据content_style选择system message
        if isinstance(system_config.get(content_style), dict):
            # 新格式：每个风格有独立的配置
            style_config = system_config.get(content_style, system_config.get("general", {}))
            system_message = style_config.get("role", "你是一个专业的内容分析助手。")

            # 添加style和tolerance说明（如果有）
            if style_config.get("style"):
                system_message += f"\n\n风格要求：{style_config['style']}"
            if style_config.get("tolerance"):
                system_message += f"\n\n容错说明：{style_config['tolerance']}"
        else:
            # 旧格式：单一的role配置
            system_message = system_config.get("role", "你是一个专业的内容分析助手。")

        # 添加约束条件
        if system_config.get("constraints"):
            constraints = "\n".join(f"- {c}" for c in system_config["constraints"])
            system_message += f"\n\n约束条件：\n{constraints}"

        # 获取model参数
        model_params = config_data["model_params"].get(prompt_type, {})

        return {
            "system": system_message,
            "user_prompt": user_prompt,
            "model_params": model_params,
            "metadata": {
                "category": category,
                "type": prompt_type,
                "locale": locale,
                "content_style": content_style,
                "version": config_data.get("version", "unknown"),
            },
        }

    def _load_prompts(self, category: str, locale: str) -> Dict:
        """加载提示词文件（带缓存）"""
        cache_key = f"{category}:{locale}"

        if cache_key in self._prompts_cache:
            return self._prompts_cache[cache_key]

        prompt_file = self.prompts_dir / category / f"{locale}.json"

        if not prompt_file.exists():
            prompt_file = self.prompts_dir / category / "zh-CN.json"
            if not prompt_file.exists():
                raise BusinessError(
                    ErrorCode.SYSTEM_ERROR, reason=f"Prompt file not found: {category}/{locale}"
                )

        with open(prompt_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._prompts_cache[cache_key] = data
        return data

    def _load_config(self, category: str) -> Dict:
        """加载配置文件（带缓存）"""
        cache_key = f"{category}:config"

        if cache_key in self._prompts_cache:
            return self._prompts_cache[cache_key]

        config_file = self.prompts_dir / category / "config.json"

        if not config_file.exists():
            return {
                "version": "1.0.0",
                "model_params": {},
            }

        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._prompts_cache[cache_key] = data
        return data

    def clear_cache(self):
        """清除缓存"""
        self._prompts_cache.clear()

    def list_available_prompts(self, category: str) -> Dict[str, Any]:
        """列出可用的提示词类型"""
        config = self._load_config(category)
        prompts_zh = self._load_prompts(category, "zh-CN")

        return {
            "category": category,
            "version": config.get("version"),
            "supported_locales": config.get("supported_locales", ["zh-CN"]),
            "prompt_types": list(prompts_zh["prompts"].keys()),
            "prompts": {
                name: {
                    "name": data.get("name"),
                    "description": data.get("description"),
                }
                for name, data in prompts_zh["prompts"].items()
            },
        }


# 全局单例
_prompt_manager = PromptManager()


def get_prompt_manager() -> PromptManager:
    """获取提示词管理器单例"""
    return _prompt_manager
