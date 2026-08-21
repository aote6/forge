"""Gemini 适配器（优化多步骤工具循环）

主要改进：
- 默认 gemini-3.7-flash（更适合 coding / agent）
- temperature 降到 0.15，减少发散和提前结束
- 工具多轮拼接更稳
- 限流重试提示更干净，降低“系统断开”体感
"""
import os
import time
from google import genai
from google.genai import types
from forge.adapters.base import BaseAdapter, Message, ToolCall


class GeminiAdapter(BaseAdapter):
    def __init__(self, model_name: str = "gemini-3.7-flash"):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY 未设置")
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def send(self, messages: list, tools: list) -> Message:
        # ---------- 工具声明 ----------
        gemini_tools = []
        if tools:
            declarations = []
            for t in tools:
                declarations.append(
                    types.FunctionDeclaration(
                        name=t["name"],
                        description=t["description"],
                        parameters=t.get("parameters", {}),
                    )
                )
            gemini_tools.append(types.Tool(function_declarations=declarations))

        # ---------- system prompt ----------
        system_instruction = ""
        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content or ""
                break

        # ---------- 对话内容 ----------
        contents = []
        for msg in messages:
            if msg.role == "user":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=msg.content or "")],
                    )
                )
            elif msg.role == "assistant":
                # 优先用原始 parts（保留 function_call 结构）
                if getattr(msg, "raw_parts", None):
                    contents.append(
                        types.Content(role="model", parts=msg.raw_parts)
                    )
                else:
                    parts = []
                    if msg.content:
                        parts.append(types.Part.from_text(text=msg.content))
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            raw_p = getattr(tc, "raw_part", None)
                            if raw_p is not None:
                                parts.append(raw_p)
                            else:
                                parts.append(
                                    types.Part.from_function_call(
                                        name=tc.name,
                                        args=tc.arguments or {},
                                    )
                                )
                    if parts:
                        contents.append(types.Content(role="model", parts=parts))
            elif msg.role == "tool":
                # 工具结果必须用 FunctionResponse
                fr = types.FunctionResponse(
                    name=msg.name or "unknown",
                    response={"result": msg.content or ""},
                )
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part(function_response=fr)],
                    )
                )

        # ---------- 生成配置 ----------
        # 低温度 + 强制倾向工具调用，减少“说完就停”
        config = types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            temperature=0.15,
            tools=gemini_tools if gemini_tools else None,
            # 有工具时尽量让模型优先考虑调用
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="AUTO"
                )
            )
            if gemini_tools
            else None,
        )

        # ---------- 调用 + 限流重试 ----------
        max_retries = 4
        response = None
        last_err = None

        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=config,
                )
                break
            except Exception as e:
                last_err = e
                err_str = str(e)
                # 限流 / 暂时不可用
                if any(
                    k in err_str
                    for k in (
                        "RESOURCE_EXHAUSTED",
                        "429",
                        "503",
                        "UNAVAILABLE",
                        "quota",
                        "rate",
                    )
                ):
                    wait = min(2 ** attempt * 2, 20)  # 2, 4, 8, 16 → 最多 20s
                    if attempt < max_retries - 1:
                        print(
                            f"\n⏳ Gemini 限流，{wait}s 后重试 ({attempt + 1}/{max_retries})...",
                            flush=True,
                        )
                        time.sleep(wait)
                        continue
                # 其他错误直接抛
                raise

        if response is None:
            raise RuntimeError(
                f"Gemini 暂不可用（已重试 {max_retries} 次）。"
                f"建议切回 zp / or / dp。原始错误: {last_err}"
            )

        # ---------- 解析结果 ----------
        text = ""
        tool_calls = []
        raw_parts = []

        if getattr(response, "candidates", None):
            for candidate in response.candidates:
                content = getattr(candidate, "content", None)
                if not content:
                    continue
                parts = getattr(content, "parts", None) or []
                raw_parts.extend(parts)
                for part in parts:
                    if getattr(part, "text", None):
                        text += part.text
                    fc = getattr(part, "function_call", None)
                    if fc:
                        args = dict(fc.args) if fc.args else {}
                        tc = ToolCall(
                            id=getattr(fc, "id", None)
                            or f"call_{len(tool_calls)}",
                            name=fc.name,
                            arguments=args,
                        )
                        # 保留原始 part，下一轮直接回传
                        tc.raw_part = part
                        tool_calls.append(tc)

        msg = Message(
            role="assistant",
            content=text.strip() or None,
            tool_calls=tool_calls if tool_calls else None,
        )
        msg.raw_parts = raw_parts
        return msg
