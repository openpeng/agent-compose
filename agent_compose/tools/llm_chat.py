"""
llm_chat 工具 - LLM 对话

支持:
- 多 provider (openai / anthropic / openai_compatible)
- 缓存机制
- 自动回退
- 温度、max_tokens 控制
"""

import hashlib
import os
import time
from typing import Any, Dict, List, Optional

# 简单内存缓存
_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL_SECONDS = 300  # 5 分钟


def _get_cache_key(args: Dict[str, Any]) -> str:
    """生成缓存键"""
    model = args.get("model", "default")
    system = (args.get("system_prompt") or "")[:50]
    prompt = (args.get("prompt") or "")[:100]
    key_str = f"{model}:{system}:{prompt}"
    return hashlib.md5(key_str.encode()).hexdigest()


def _get_from_cache(cache_key: str) -> Optional[str]:
    """从缓存获取"""
    entry = _cache.get(cache_key)
    if entry and (time.time() - entry["time"]) < CACHE_TTL_SECONDS:
        return entry["content"]
    return None


def _set_cache(cache_key: str, content: str) -> None:
    """设置缓存"""
    _cache[cache_key] = {"content": content, "time": time.time()}


def _cleanup_cache() -> None:
    """清理过期缓存"""
    now = time.time()
    expired = [k for k, v in _cache.items() if now - v["time"] > CACHE_TTL_SECONDS]
    for k in expired:
        del _cache[k]


def _format_error(provider: str, model: str, status: Optional[int], message: str) -> str:
    """格式化 LLM 错误"""
    if status and 400 <= status < 500:
        return f"llm_chat: Client error ({status}) from {provider}/{model}: {message}. Check your API key and model name."
    if status and 500 <= status < 600:
        return f"llm_chat: Server error ({status}) from {provider}/{model}: {message}. The service may be temporarily unavailable."
    return f"llm_chat: Error from {provider}/{model}: {message}"


async def _call_openai(
    prompt: str,
    system_prompt: Optional[str],
    model: str,
    temperature: float,
    max_tokens: int,
    api_key: Optional[str],
    api_base: Optional[str],
) -> Dict[str, Any]:
    """调用 OpenAI API"""
    import httpx

    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("llm_chat: OpenAI API key not provided. Set OPENAI_API_KEY environment variable or pass api_key.")

    base = api_base or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    url = f"{base}/chat/completions"

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, json=payload, headers=headers)

        if response.status_code != 200:
            raise RuntimeError(_format_error("openai", model, response.status_code, response.text[:200]))

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)

        return {"content": content, "tokens": tokens}


async def _call_anthropic(
    prompt: str,
    system_prompt: Optional[str],
    model: str,
    temperature: float,
    max_tokens: int,
    api_key: Optional[str],
    api_base: Optional[str],
) -> Dict[str, Any]:
    """调用 Anthropic API"""
    import httpx

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("llm_chat: Anthropic API key not provided. Set ANTHROPIC_API_KEY environment variable or pass api_key.")

    base = api_base or os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    url = f"{base}/v1/messages"

    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }

    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system_prompt:
        payload["system"] = system_prompt

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, json=payload, headers=headers)

        if response.status_code != 200:
            raise RuntimeError(_format_error("anthropic", model, response.status_code, response.text[:200]))

        data = response.json()
        content = data["content"][0]["text"]
        tokens = data.get("usage", {}).get("input_tokens", 0) + data.get("usage", {}).get("output_tokens", 0)

        return {"content": content, "tokens": tokens}


async def _call_openai_compatible(
    prompt: str,
    system_prompt: Optional[str],
    model: str,
    temperature: float,
    max_tokens: int,
    api_key: Optional[str],
    api_base: Optional[str],
) -> Dict[str, Any]:
    """调用 OpenAI 兼容 API"""
    return await _call_openai(prompt, system_prompt, model, temperature, max_tokens, api_key, api_base)


async def llm_chat_tool(args: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    LLM 对话

    Args:
        args: {
            "prompt": str,              # 必需: 用户提示
            "system_prompt": str,       # 可选: 系统提示
            "model": str,               # 可选: 模型名称
            "temperature": float,       # 可选: 温度, 默认 0.7
            "max_tokens": int,          # 可选: 最大 token 数, 默认 4096
            "provider": str,            # 可选: "openai" | "anthropic" | "openai_compatible"
            "api_key": str,             # 可选: API 密钥
            "api_base": str,            # 可选: API 基础 URL
        }

    Returns:
        {
            "content": str,             # LLM 生成的文本内容
            "model": str,               # 实际使用的模型
            "tokens_used": int,         # 输入+输出 token 总数
            "duration_ms": float,       # 请求耗时
        }
    """
    prompt = args.get("prompt")
    if not prompt or not isinstance(prompt, str):
        raise ValueError("llm_chat: 'prompt' parameter is required and must be a string")

    # 检查缓存
    cache_key = _get_cache_key(args)
    cached = _get_from_cache(cache_key)
    if cached is not None:
        return {
            "content": cached,
            "model": args.get("model", "cached"),
            "tokens_used": 0,
            "duration_ms": 0,
        }

    system_prompt = args.get("system_prompt")
    temperature = args.get("temperature", 0.7)
    max_tokens = args.get("max_tokens", 4096)
    provider = args.get("provider")
    api_key = args.get("api_key")
    api_base = args.get("api_base")

    # 自动检测 provider
    providers_to_try: List[str] = []
    if provider:
        providers_to_try = [provider]
    else:
        # 优先顺序
        if os.environ.get("ANTHROPIC_API_KEY"):
            providers_to_try.append("anthropic")
        if os.environ.get("OPENAI_API_KEY"):
            providers_to_try.append("openai")
        if os.environ.get("OPENAI_COMPATIBLE_API_KEY") or os.environ.get("OPENAI_COMPATIBLE_BASE_URL"):
            providers_to_try.append("openai_compatible")
        if not providers_to_try:
            raise ValueError("llm_chat: No LLM provider configured. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or specify provider and api_key.")

    # 模型选择
    model = args.get("model")
    if not model:
        model_defaults = {
            "anthropic": "claude-3-sonnet-20240229",
            "openai": "gpt-4o",
            "openai_compatible": "default",
        }
        model = model_defaults.get(providers_to_try[0], "default")

    start_time = time.time()
    last_error = None

    for prov in providers_to_try:
        try:
            if prov == "anthropic":
                result = await _call_anthropic(
                    prompt, system_prompt, model, temperature, max_tokens, api_key, api_base
                )
            elif prov == "openai":
                result = await _call_openai(
                    prompt, system_prompt, model, temperature, max_tokens, api_key, api_base
                )
            else:
                result = await _call_openai_compatible(
                    prompt, system_prompt, model, temperature, max_tokens, api_key, api_base
                )

            duration_ms = (time.time() - start_time) * 1000

            # 写入缓存
            _set_cache(cache_key, result["content"])
            _cleanup_cache()

            return {
                "content": result["content"],
                "model": model,
                "tokens_used": result.get("tokens", 0),
                "duration_ms": round(duration_ms, 2),
            }

        except (ValueError, RuntimeError) as e:
            last_error = e
            # 5xx 错误或超时才回退
            error_str = str(e)
            if "Server error" in error_str or "timed out" in error_str.lower():
                continue
            raise
        except Exception as e:
            last_error = e
            continue

    # 所有 provider 都失败
    raise RuntimeError(f"llm_chat: All providers failed. Last error: {last_error}")
