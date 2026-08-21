"""DeepSeek 适配器 - OpenAI 兼容协议（小优化）

- 支持环境变量 DEEPSEEK_MODEL 换模型
- 简单重试应对偶发 429 / 网络抖动
- 保持原有工具调用逻辑
"""
import json
import os
import time
from openai import OpenAI
from forge.adapters.base import BaseAdapter, Message, ToolCall


class DeepSeekAdapter(BaseAdapter):
    def __init__(self, model_name: str = "deepseek-chat"):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY 未设置")
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
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

        kwargs = {
            "model": self.model_name,
            "messages": api_messages,
            "temperature": 0.1,
        }
        if api_tools:
            kwargs["tools"] = api_tools

        # 简单重试：偶发 429 / 网络抖动
        max_retries = 3
        last_err = None
        response = None
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(**kwargs)
                break
            except Exception as e:
                last_err = e
                err_str = str(e).lower()
                if any(
                    k in err_str
                    for k in ("429", "rate", "timeout", "connect", "503", "unavailable")
                ):
                    wait = 2**attempt  # 1, 2, 4
                    if attempt < max_retries - 1:
                        print(
                            f"\n⏳ DeepSeek 限流/网络，{wait}s 后重试 "
                            f"({attempt + 1}/{max_retries})...",
                            flush=True,
                        )
                        time.sleep(wait)
                        continue
                raise

        if response is None:
            raise RuntimeError(
                f"DeepSeek 调用失败（已重试 {max_retries} 次）: {last_err}"
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
