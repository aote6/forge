#!/usr/bin/env python3
"""Forge 入口 - Gemini（简单对话 / 轻量任务）

多步骤工具循环仍推荐用 zp（智谱）或 or（OpenRouter）。
Gemini 更适合解释、聊天、单轮问答。
"""
import os
import sys

from forge.workspace import Workspace
from forge.memory import MemoryStore
from forge.runtime import Runtime
from forge.events import EventType
from forge.adapters.gemini import GeminiAdapter

project_root = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

# 默认最新 3.7-flash，可用环境变量覆盖
# 例如：GEMINI_MODEL=gemini-3.5-flash-lite python3 gg.py
adapter = GeminiAdapter(
    model_name=os.environ.get("GEMINI_MODEL", "gemini-3.7-flash")
)
tag = "Gemini"


def main():
    print(f"🔌 使用 {tag} ({adapter.model_name})")
    print(f"📁 项目: {os.path.abspath(os.path.expanduser(project_root))}")
    workspace = Workspace(project_root=project_root)
    memory = MemoryStore()
    runtime = Runtime(adapter, workspace, memory)

    def _on_tool_start(e):
        print(f"\n🔧 [{e.data.get('name')}] ...", end="", flush=True)

    def _on_tool_end(e):
        ok = e.data.get("success")
        mark = "✅" if ok else "❌"
        print(f" {mark}", flush=True)
        disp = (e.data.get("display") or "").strip()
        if not disp:
            return
        lines = disp.splitlines()
        max_lines, max_chars = 18, 1200
        shown = lines[:max_lines]
        body = "\n".join(shown)
        if len(body) > max_chars:
            body = body[:max_chars] + "\n..."
        elif len(lines) > max_lines:
            body = body + f"\n...(+{len(lines) - max_lines} lines, 输入 last 看全文)"
        print(body, flush=True)

    runtime.on(EventType.TOOL_CALL_START, _on_tool_start)
    runtime.on(EventType.TOOL_CALL_END, _on_tool_end)

    print("⚒️ Forge | 工具循环 | 输入 q 退出")
    print("  提示：多步骤复杂任务建议用 zp / or，Gemini 更适合简单对话")
    print("=" * 40)

    while True:
        try:
            user_input = input("\n💬 > ")
            if not user_input.strip():
                continue
            if user_input.strip().lower() in ("exit", "quit", "q"):
                print("👋")
                break
            response = runtime.run(user_input)
            n = getattr(runtime, "_last_tool_calls", 0)
            print(f"\n📊 本次工具调用: {n}")
            if response:
                print(f"\n🤖 {response}")
        except KeyboardInterrupt:
            print("\n👋")
            break
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"\n❌ 会话异常，为避免继续损坏状态已终止本次会话：{e}")
            try:
                runtime.save_session_summary()
                _save_conversation_history(runtime)
            except Exception:
                pass
            break


if __name__ == "__main__":
    main()
