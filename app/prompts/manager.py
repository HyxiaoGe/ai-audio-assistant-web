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
    ) -> Dict[str, Any]:
        """获取提示词

        Args:
            category: 类别（如 summary）
            prompt_type: 提示词类型（如 overview, key_points, action_items）
            locale: 语言（如 zh-CN, en-US）
            variables: 模板变量（如 {transcript}）

        Returns:
            包含 system, user_prompt, model_params 的字典
        """
        prompt_data = self._load_prompts(category, locale)
        config_data = self._load_config(category)

        if prompt_type not in prompt_data["prompts"]:
            raise BusinessError(
                ErrorCode.INVALID_PARAMETER,
                detail=f"Unknown prompt type: {prompt_type}"
            )

        prompt_template = prompt_data["prompts"][prompt_type]["template"]
        system_config = prompt_data["system"]

        if variables:
            user_prompt = prompt_template.format(**variables)
        else:
            user_prompt = prompt_template

        content_style = variables.get("content_style", "meeting") if variables else "meeting"

        if isinstance(system_config.get("meeting"), dict):
            style_config = system_config.get(content_style, system_config.get("general", {}))
            system_message = style_config.get("role", "你是一个专业的内容分析助手。")
        else:
            system_message = system_config.get("role", "你是一个专业的内容分析助手。")

        if system_config.get("constraints"):
            constraints = "\n".join(f"- {c}" for c in system_config["constraints"])
            system_message += f"\n\n约束条件：\n{constraints}"

        model_params = config_data["model_params"].get(prompt_type, {})

        return {
            "system": system_message,
            "user_prompt": user_prompt,
            "model_params": model_params,
            "metadata": {
                "category": category,
                "type": prompt_type,
                "locale": locale,
                "version": config_data.get("version", "unknown"),
            }
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
                    ErrorCode.SYSTEM_ERROR,
                    reason=f"Prompt file not found: {category}/{locale}"
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
            }
        }


# 全局单例
_prompt_manager = PromptManager()


def get_prompt_manager() -> PromptManager:
    """获取提示词管理器单例"""
    return _prompt_manager
