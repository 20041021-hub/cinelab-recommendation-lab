"""实验六 S2：外部大模型客户端（OpenAI 兼容的 /chat/completions）。

只做三件事：读配置、发一次请求、把异常翻译成可解释的错误码。所有"电影数据"
都不在这里产生——模型只能通过 ``backend/app/assistant.py`` 里的受控工具拿数据。

配置（环境变量，全部可选，缺省指向 DeepSeek）：

* ``APP_LLM_BASE_URL``  默认 ``https://api.deepseek.com``
* ``APP_LLM_MODEL``     默认 ``deepseek-chat``
* ``APP_LLM_KEY_FILE``  存放 API Key 的文件（默认 ``/app/llm-key/llm-api-key.txt``）
* ``APP_LLM_API_KEY``   直接给 Key（优先级高于文件，方便一次性联调）
* ``APP_LLM_TIMEOUT``   单次请求超时秒数，默认 30
* ``APP_LLM_MODE``      ``live``（默认，真实调用）或 ``stub``（只给自动化测试用的
  固定应答，响应里会带 ``provider="stub"``，绝不用来充当真实演示）

Key 只从文件/环境读，不写日志、不回显、不出现在错误信息里。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_KEY_FILE = "/app/llm-key/llm-api-key.txt"
DEFAULT_TIMEOUT = 30.0
MAX_TOOL_ROUNDS = 3


class LLMUnavailable(Exception):
    """外部模型不可用（缺 Key / 网络失败 / 超时 / 非 200 / 返回体不合法）。"""

    def __init__(self, reason: str, detail: str | None = None):
        self.reason, self.detail = reason, detail
        super().__init__(reason)


def base_url() -> str:
    return (os.environ.get("APP_LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def model() -> str:
    return os.environ.get("APP_LLM_MODEL") or DEFAULT_MODEL


def mode() -> str:
    return (os.environ.get("APP_LLM_MODE") or "live").strip().lower()


def timeout_seconds() -> float:
    try:
        return float(os.environ.get("APP_LLM_TIMEOUT") or DEFAULT_TIMEOUT)
    except ValueError:
        return DEFAULT_TIMEOUT


def key_file() -> Path:
    return Path(os.environ.get("APP_LLM_KEY_FILE") or DEFAULT_KEY_FILE)


def api_key() -> str | None:
    """读取 Key：环境变量优先，其次 Key 文件；都没有则返回 None。"""
    direct = (os.environ.get("APP_LLM_API_KEY") or "").strip()
    if direct:
        return direct
    path = key_file()
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def configured() -> bool:
    return mode() == "stub" or api_key() is not None


def status() -> dict[str, Any]:
    """给诊断接口用：只报告"有没有配置"，绝不回显 Key 本身。"""
    path = key_file()
    return {
        "mode": mode(),
        "base_url": base_url(),
        "model": model(),
        "key_configured": api_key() is not None,
        "key_file": str(path),
        "key_file_exists": path.exists(),
        "timeout_seconds": timeout_seconds(),
        "max_tool_rounds": MAX_TOOL_ROUNDS,
    }


def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """调用一次对话补全，返回 ``(assistant 消息, 本次 token 用量)``。

    用量直接取服务端返回的 ``usage``（prompt/completion/total tokens），
    助手接口会把它累计后回显，用户能看见一次提问到底花了多少 token。

    live 模式走真实 HTTP；stub 模式由 ``stub_reply`` 生成固定应答，仅供测试。
    """
    if mode() == "stub":
        return stub_reply(messages), {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "stub": True}
    key = api_key()
    if key is None:
        raise LLMUnavailable("missing_key", f"未找到 API Key（{key_file()}）")
    payload: dict[str, Any] = {"model": model(), "messages": messages, "stream": False}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    try:
        response = httpx.post(
            f"{base_url()}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout_seconds(),
        )
    except httpx.TimeoutException as exc:
        raise LLMUnavailable("timeout", f"{timeout_seconds():.0f} 秒内没有响应") from exc
    except httpx.HTTPError as exc:
        raise LLMUnavailable("network_error", type(exc).__name__) from exc
    if response.status_code != 200:
        raise LLMUnavailable("http_error", f"HTTP {response.status_code}")
    try:
        body = response.json()
        message = body["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMUnavailable("bad_response", "响应体不是 OpenAI 兼容格式") from exc
    if not isinstance(message, dict):
        raise LLMUnavailable("bad_response", "assistant 消息不是对象")
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return message, {key_: int(usage.get(key_) or 0)
                     for key_ in ("prompt_tokens", "completion_tokens", "total_tokens")}


# ---- stub（只给自动化测试用；响应里会标注 provider=stub） -----------------------

def stub_reply(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """按问题里的关键词返回固定的工具调用，用来在无 Key 时验证整条管道。"""
    question = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
    tool_messages = [m for m in messages if m.get("role") == "tool"]
    known = {"search_movies", "similar_movies", "recommend_movies", "my_history"}

    def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"stub-{name}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}

    if not tool_messages:
        if "相似" in question or "像" in question:
            return call("similar_movies", {"movie_id": "1", "limit": 5})
        if "推荐" in question:
            return call("recommend_movies", {"algorithm": "itemcf", "limit": 5})
        if "看过" in question or "我的评分" in question:
            return call("my_history", {"limit": 5})
        if "不存在的工具" in question:
            return call("weather_today", {"city": "北京"})
        if "没有结果" in question:
            return call("search_movies", {"genres": ["Film-Noir"], "year_min": 2028, "year_max": 2030, "limit": 5})
        return call("search_movies", {"genres": ["Animation"], "year_min": 1990, "year_max": 2000, "limit": 5})
    names = [m.get("name") for m in tool_messages]
    return {"role": "assistant", "content": f"[stub] 已通过受控工具（{'、'.join(str(n) for n in names)}）"
                                            f"取得真实数据，共 {len(tool_messages)} 次调用。"}