#!/usr/bin/env python3
"""Forge 入口 - 默认 Gemini"""
import sys, os
from forge.workspace import Workspace
from forge.memory import MemoryStore
from forge.runtime import Runtime
from forge.events import EventType
from forge.adapters.gemini import GeminiAdapter

project_root = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
adapter = GeminiAdapter(model_name="gemini-3.5-flash")
tag = "Gemini"

def main():
    print(f"🔌 使用 {tag}")
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

    print("⚒️ Forge Engineering Orchestrator | 输入 q 退出")
    print("  工具循环 | 输出即时显示（与 dp 一致）")
    print("=" * 40)

    while True:
        try:
            user_input = input("\n💬 > ")
            if not user_input.strip():
                continue
            if user_input.strip().lower() in ("exit", "quit", "q"):
                print("👋")
                break
            # Production path: unique Engineering Orchestrator
            response = runtime.run(user_input)
            if response:
                print(f"\n🤖 {response}")
        except KeyboardInterrupt:
            print("\n👋")
            break
        except Exception as e:
            print(f"\n❌ {e}")

if __name__ == "__main__":
    main()
