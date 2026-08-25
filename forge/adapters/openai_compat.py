"""通用 OpenAI 兼容适配器
支持 DeepSeek / 智谱 / OpenRouter / Groq / NVIDIA NIM 等所有 OpenAI 协议接口。
"""
import json
import os
import time
from openai import OpenAI
from forge.adapters.base import BaseAdapter, Message, ToolCall


def _is_retryable_error(e: BaseException) -> bool:
    """判定是否可重试：仅 429 或 5xx；其它 4xx（400/401/404/422...）不重试。

    对齐 deepseek.py 的重试策略（指数退避、最多 3 次），但判定更精确：
    优先看 openai.APIStatusError 的 status_code；无 status_code 的传输层错误
    （连接/超时）按网络抖动处理（非 4xx，可重试）。
    """
    status = getattr(e, "status_code", None)
    if status is not None:
        return status == 429 or (500 <= int(status) < 600)
    err_str = str(e).lower()
    return any(k in err_str for k in ("timeout", "connect", "unavailable", "connection"))


class OpenAICompatAdapter(BaseAdapter):
    def __init__(
        self,
        model_name: str,
        api_key_env: str,
        base_url: str,
        default_headers: dict | None = None,
    ):
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(f"{api_key_env} 未设置，请先 export {api_key_env}=你的密钥")
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=default_headers or {},
        )
        self.model_name = model_name

    def send(self, messages: list, tools: list) -> Message:
        api_messages = []
        for msg in messages:
            if msg.role == "system":
                api_messages.append({"role": "system", "content": msg.content or ""})
            elif msg.role == "user":
                api_messages.append({"role": "user", "content": msg.content or ""})
            elif msg.role == "assistant":
                m = {"role": "assistant", "content": msg.content or ""}
                if msg.tool_calls:
                    m["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                api_messages.append(m)
            elif msg.role == "tool":
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id or "",
                        "content": msg.content or "",
                    }
                )

        api_tools = []
        for t in tools:
            api_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t.get("parameters", {}),
                    },
                }
            )

        kwargs = {
            "model": self.model_name,
            "messages": api_messages,
            "temperature": 0.1,
        }
        if api_tools:
            kwargs["tools"] = api_tools

        # 指数退避重试：仅 429 / 5xx（对齐 deepseek.py，最多 3 次，间隔 1/2/4s）。
        max_retries = 3
        last_err = None
        response = None
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(**kwargs)
                break
            except Exception as e:
                last_err = e
                if not _is_retryable_error(e) or attempt >= max_retries - 1:
                    raise
                wait = 2 ** attempt  # 1, 2, 4
                print(
                    f"\n⏳ 模型网关限流/服务端错误，{wait}s 后重试 "
                    f"({attempt + 1}/{max_retries})...",
                    flush=True,
                )
                time.sleep(wait)

        if response is None:
            raise RuntimeError(
                f"模型网关调用失败（已重试 {max_retries} 次）: {last_err}"
            )

        if not response.choices:
            err_info = getattr(response, "error", None) or getattr(response, "model_extra", {}).get("error", None)
            raise RuntimeError(
                f"模型网关返回了空响应（choices 为空），可能是路由失败/限流/模型不可用。"
                f"原始错误信息: {err_info}"
            )

        choice = response.choices[0]

        tool_calls = None
        if choice.message.tool_calls:
            tool_calls = []
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    return Message(
                        role="assistant",
                        content="工具参数格式错误，请重新生成合法 JSON 参数。",
                        tool_calls=None,
                    )
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=args,
                    )
                )

        return Message(
            role="assistant",
            content=choice.message.content,
            tool_calls=tool_calls,
        )

    def _build_chat_payload(self, messages: list, tools: list):
        """Shared message/tool payload for send and send_stream."""
        import json
        api_messages = []
        for msg in messages:
            if msg.role == "system":
                api_messages.append({"role": "system", "content": msg.content or ""})
            elif msg.role == "user":
                api_messages.append({"role": "user", "content": msg.content or ""})
            elif msg.role == "assistant":
                m = {"role": "assistant", "content": msg.content or ""}
                if msg.tool_calls:
                    m["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(
                                    tc.arguments, ensure_ascii=False
                                ),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                api_messages.append(m)
            elif msg.role == "tool":
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id or "",
                        "content": msg.content or "",
                    }
                )
        api_tools = []
        for t in tools:
            api_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t.get("parameters", {}),
                    },
                }
            )
        kwargs_base = {"temperature": 0.1}
        return api_messages, api_tools, kwargs_base

    def send_stream(self, messages: list, tools: list, on_text_delta=None) -> Message:
        """Stream text deltas; return a complete Message (tool_calls fully assembled)."""
        api_messages, api_tools, kwargs_base = self._build_chat_payload(messages, tools)
        try:
            from forge.adapters.stream_util import complete_chat_stream

            return complete_chat_stream(
                self.client,
                model=self.model_name,
                api_messages=api_messages,
                api_tools=api_tools or None,
                temperature=kwargs_base.get("temperature", 0.1),
                on_text_delta=on_text_delta,
            )
        except Exception:
            msg = self.send(messages, tools)
            if on_text_delta and msg.content:
                try:
                    on_text_delta(msg.content)
                except Exception:
                    pass
            return msg
