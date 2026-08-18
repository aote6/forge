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

    runtime.on(EventType.TOOL_CALL_START, lambda e: print(
        f"\n🔧 [{e.data['name']}] ...", end="", flush=True
    ))
    runtime.on(EventType.TOOL_CALL_END, lambda e: print(
        f" {'✅' if e.data['success'] else '❌'}"
    ))

    print("⚒️ Forge Engineering Orchestrator | 输入 q 退出")
    print("  工程任务走 Runtime.run → EngineeringOrchestrator（六 Phase 闭环）")
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
