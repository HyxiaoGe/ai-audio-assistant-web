# 为其他LLM服务添加generate方法指南

## 背景

我们已经在`DeepSeekLLMService`中实现了`generate`方法。其他LLM服务也需要添加相同的方法。

## 需要修改的文件

- `app/services/llm/qwen.py`
- `app/services/llm/doubao.py`
- `app/services/llm/moonshot.py`
- `app/services/llm/openrouter.py`

## 实现模板

在每个LLM服务类中，在`chat`方法之前添加以下方法：

```python
@monitor("llm", "{provider_name}")  # 替换为具体provider名称
async def generate(
    self,
    prompt: str,
    system_message: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    **kwargs: Any,
) -> str:
    """通用文本生成

    Args:
        prompt: 用户提示词
        system_message: 系统消息（可选）
        temperature: 温度参数（可选）
        max_tokens: 最大token数（可选）
        **kwargs: 其他参数

    Returns:
        生成的文本
    """
    messages = []
    if system_message:
        messages.append({"role": "system", "content": system_message})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": self._model,  # 或 self._model_id，根据实际属性名调整
        "messages": messages,
        "max_tokens": max_tokens or self._max_tokens,  # 根据实际属性名调整
        "temperature": temperature or 0.7,
    }
    headers = {"Authorization": f"Bearer {self._api_key}"}  # 根据实际认证方式调整

    return await self._call_llm_api(payload, headers)  # 或相应的内部方法
```

## 注意事项

1. **属性名称差异**：不同服务可能使用不同的属性名（如`_model` vs `_model_id`）
2. **认证方式差异**：有些服务可能不是Bearer token认证
3. **API endpoint差异**：确保使用正确的内部API调用方法
4. **默认值**：根据各服务的特点调整默认temperature和max_tokens

## 快速实施方法

由于所有服务都已经实现了`chat`方法，`generate`方法本质上是`chat`的简化封装。

**最简实现：**

```python
async def generate(
    self,
    prompt: str,
    system_message: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    **kwargs: Any,
) -> str:
    """通用文本生成"""
    messages = []
    if system_message:
        messages.append({"role": "system", "content": system_message})
    messages.append({"role": "user", "content": prompt})

    return await self.chat(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs
    )
```

这种方式复用了已有的`chat`实现，代码更简洁。

## 验证

添加后，确保：
1. 方法签名与base.py中的抽象方法一致
2. 可以正常调用并返回结果
3. 错误处理与现有方法保持一致
