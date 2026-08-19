#!/usr/bin/env python3
"""Forge 入口 - 强制 DeepSeek；生产路径 = Runtime 工具循环。"""
import sys, os
from forge.workspace import Workspace
from forge.memory import MemoryStore
from forge.runtime import Runtime
from forge.events import EventType
from forge.adapters.deepseek import DeepSeekAdapter

project_root = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
adapter = DeepSeekAdapter(model_name="deepseek-v4-flash")
tag = "DeepSeek"


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

    print("⚒️ Forge | 工具循环（只读 + World/文件 mutation）| 输入 q 退出")
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
            if response:
                print(f"\n🤖 {response}")
        except KeyboardInterrupt:
            print("\n👋")
            break
        except Exception as e:
            print(f"\n❌ {e}")

if __name__ == "__main__":
    main()
