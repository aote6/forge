"""通用 OpenAI 兼容适配器
支持 DeepSeek / 智谱 / OpenRouter / Groq / NVIDIA NIM 等所有 OpenAI 协议接口。
"""
import json
import os
from openai import OpenAI
from forge.adapters.base import BaseAdapter, Message, ToolCall


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

        response = self.client.chat.completions.create(**kwargs)

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
