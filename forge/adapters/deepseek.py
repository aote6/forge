"""DeepSeek 适配器 - OpenAI 兼容协议"""
import os
from openai import OpenAI
from forge.adapters.base import BaseAdapter, Message, ToolCall


class DeepSeekAdapter(BaseAdapter):
    def __init__(self, model_name: str = "deepseek-chat"):
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY 未设置")
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com"
        )
        self.model_name = model_name
    
    def send(self, messages: list, tools: list) -> Message:
        # 转换消息格式
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
                                "arguments": str(tc.arguments)
                            }
                        }
                        for tc in msg.tool_calls
                    ]
                api_messages.append(m)
            elif msg.role == "tool":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id or "",
                    "content": msg.content or ""
                })
        
        # 转换工具格式
        api_tools = []
        for t in tools:
            api_tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t.get("parameters", {})
                }
            })
        
        # 调用 API
        kwargs = {
            "model": self.model_name,
            "messages": api_messages,
            "temperature": 0.4,
        }
        if api_tools:
            kwargs["tools"] = api_tools
        
        response = self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        
        # 解析 ToolCall
        tool_calls = None
        if choice.message.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=eval(tc.function.arguments)
                )
                for tc in choice.message.tool_calls
            ]
        
        return Message(
            role="assistant",
            content=choice.message.content,
            tool_calls=tool_calls
        )
