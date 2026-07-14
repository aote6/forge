"""Gemini 适配器 - 无状态版本"""
import os
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
    
    def _build_tools(self, tool_declarations: list) -> list:
        if not tool_declarations:
            return []
        declarations = []
        for t in tool_declarations:
            declarations.append(types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=t.get("parameters", {})
            ))
        return [types.Tool(function_declarations=declarations)]
    
    def _extract_system_instruction(self, messages: list) -> str:
        for msg in messages:
            if msg.role == "system" and msg.content:
                return msg.content
        return ""
    
    def send(self, messages: list, tools: list) -> Message:
        system_instruction = self._extract_system_instruction(messages)
        gemini_tools = self._build_tools(tools)
        
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.4,
            tools=gemini_tools,
        )
        
        chat = self.client.chats.create(model=self.model_name, config=config)
        
        # 逐条发送非 system 消息
        last_response = None
        for msg in messages:
            if msg.role == "system":
                continue
            elif msg.role == "user":
                last_response = chat.send_message(
                    types.Content(role="user", parts=[types.Part(text=msg.content or "")])
                )
            elif msg.role == "assistant":
                parts = []
                if msg.content:
                    parts.append(types.Part(text=msg.content))
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        parts.append(types.Part(
                            function_call=types.FunctionCall(
                                id=tc.id, name=tc.name, args=tc.arguments
                            )
                        ))
                last_response = chat.send_message(
                    types.Content(role="model", parts=parts)
                )
            elif msg.role == "tool":
                last_response = chat.send_message(
                    types.Content(role="user", parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=msg.tool_call_id or "",
                                name=msg.name or "",
                                response={"result": msg.content or ""}
                            )
                        )
                    ])
                )
        
        if last_response is None:
            last_response = chat.send_message("继续")
        
        # 提取 ToolCall
        tool_calls = []
        if hasattr(last_response, 'candidates') and last_response.candidates:
            for candidate in last_response.candidates:
                if hasattr(candidate, 'content') and candidate.content:
                    for part in candidate.content.parts:
                        if hasattr(part, 'function_call') and part.function_call:
                            fc = part.function_call
                            tool_calls.append(ToolCall(
                                id=fc.id if hasattr(fc, 'id') else "",
                                name=fc.name,
                                arguments=dict(fc.args) if fc.args else {}
                            ))
        
        # 提取文本
        text = ""
        if hasattr(last_response, 'text') and last_response.text:
            text = last_response.text
        elif hasattr(last_response, 'candidates') and last_response.candidates:
            for candidate in last_response.candidates:
                if hasattr(candidate, 'content') and candidate.content:
                    for part in candidate.content.parts:
                        if hasattr(part, 'text') and part.text:
                            text += part.text
        
        return Message(
            role="assistant",
            content=text or None,
            tool_calls=tool_calls or None
        )
