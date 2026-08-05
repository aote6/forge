"""Gemini 适配器 - 简化版"""
import os
import time
from google import genai
from google.genai import types
from forge.adapters.base import BaseAdapter, Message, ToolCall


class GeminiAdapter(BaseAdapter):
    def __init__(self, model_name: str = "gemini-2.5-flash"):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY 未设置")
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
    
    def send(self, messages: list, tools: list) -> Message:
        system_instruction = ""
        user_parts = []
        tool_results = []
        
        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content or ""
            elif msg.role == "user":
                user_parts.append(msg.content or "")
            elif msg.role == "tool":
                tool_results.append({
                    "id": msg.tool_call_id,
                    "name": msg.name,
                    "result": msg.content or ""
                })
        
        gemini_tools = []
        for t in tools:
            gemini_tools.append(types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters=t.get("parameters", {})
                )
            ]))
        
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.4,
            tools=gemini_tools,
        )
        
        prompt = "\n".join(user_parts)
        if tool_results:
            prompt += "\n\n工具返回结果:\n"
            for tr in tool_results:
                prompt += f"[{tr['name']}] {tr['result']}\n"
        
        max_retries = 3
        response = None
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config
                )
                break
            except Exception as e:
                if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
                    wait = 15 * (attempt + 1)
                    print(f"\n⏳ 触发限额，等待 {wait} 秒后重试（{attempt+1}/{max_retries}）...")
                    time.sleep(wait)
                    continue
                raise
        if response is None:
            raise RuntimeError("多次重试后仍然限额，请稍后再试或切换 DeepSeek")
        
        tool_calls = None
        text = response.text or ""
        
        if hasattr(response, 'candidates') and response.candidates:
            for candidate in response.candidates:
                if hasattr(candidate, 'content') and candidate.content:
                    for part in candidate.content.parts:
                        if hasattr(part, 'function_call') and part.function_call:
                            fc = part.function_call
                            tool_calls = [ToolCall(
                                id=fc.id if hasattr(fc, 'id') else "1",
                                name=fc.name,
                                arguments=dict(fc.args) if fc.args else {}
                            )]
        
        return Message(role="assistant", content=text, tool_calls=tool_calls)
