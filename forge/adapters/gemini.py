"""Gemini 适配器"""
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
        gemini_tools = []
        if tools:
            declarations = []
            for t in tools:
                declarations.append(types.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters=t.get("parameters", {})
                ))
            gemini_tools.append(types.Tool(function_declarations=declarations))

        system_instruction = ""
        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content or ""
                break

        contents = []
        for msg in messages:
            if msg.role == "user":
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=msg.content or "")]
                ))
            elif msg.role == "assistant":
                if hasattr(msg, "raw_parts") and msg.raw_parts:
                    contents.append(types.Content(role="model", parts=msg.raw_parts))
                else:
                    parts = []
                    if msg.content:
                        parts.append(types.Part.from_text(text=msg.content))
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            raw_p = getattr(tc, "raw_part", None)
                            if raw_p:
                                parts.append(raw_p)
                            else:
                                parts.append(types.Part.from_function_call(
                                    name=tc.name,
                                    args=tc.arguments
                                ))
                    if parts:
                        contents.append(types.Content(role="model", parts=parts))
            elif msg.role == "tool":
                fr = types.FunctionResponse(
                    name=msg.name or "unknown",
                    response={"result": msg.content or ""}
                )
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(function_response=fr)]
                ))

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.4,
            tools=gemini_tools if gemini_tools else None,
        )

        max_retries = 5
        response = None
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=config
                )
                break
            except Exception as e:
                err_str = str(e)
                if any(k in err_str for k in ["RESOURCE_EXHAUSTED", "429", "503", "UNAVAILABLE"]):
                    wait = (2 ** attempt) * 3
                    print(f"\n⏳ 服务繁忙，{wait} 秒后重试（{attempt+1}/{max_retries}）...")
                    time.sleep(wait)
                    continue
                raise

        if response is None:
            raise RuntimeError("Gemini 服务暂不可用，建议切回 DeepSeek (`dp`) 模式")

        text = ""
        tool_calls = []
        raw_parts = []

        if hasattr(response, 'candidates') and response.candidates:
            for candidate in response.candidates:
                if hasattr(candidate, 'content') and candidate.content:
                    if hasattr(candidate.content, 'parts') and candidate.content.parts:
                        raw_parts.extend(candidate.content.parts)
                        for part in candidate.content.parts:
                            if hasattr(part, 'text') and part.text:
                                text += part.text
                            if hasattr(part, 'function_call') and part.function_call:
                                fc = part.function_call
                                args = dict(fc.args) if fc.args else {}
                                tc = ToolCall(
                                    id=fc.id if hasattr(fc, 'id') and fc.id else str(len(tool_calls)),
                                    name=fc.name,
                                    arguments=args
                                )
                                tc.raw_part = part
                                tool_calls.append(tc)

        msg = Message(
            role="assistant",
            content=text.strip() or None,
            tool_calls=tool_calls if tool_calls else None
        )
        msg.raw_parts = raw_parts
        return msg
