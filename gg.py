#!/usr/bin/env python3
"""Forge 入口脚本"""
import sys
import os
from forge.adapters.deepseek import DeepSeekAdapter
from forge.workspace import Workspace
from forge.memory import MemoryStore
from forge.runtime import Runtime
from forge.events import EventType


def main():
    # 优先用 DeepSeek，没有 key 则回退 Gemini
    if os.environ.get("DEEPSEEK_API_KEY"):
        adapter = DeepSeekAdapter(model_name="deepseek-chat")
        print("🔌 使用 DeepSeek")
    elif os.environ.get("GEMINI_API_KEY"):
        from forge.adapters.gemini import GeminiAdapter
        adapter = GeminiAdapter(model_name="gemini-2.5-flash")
        print("🔌 使用 Gemini")
    else:
        print("❌ 请设置 DEEPSEEK_API_KEY 或 GEMINI_API_KEY")
        sys.exit(1)
    
    workspace = Workspace(project_root=".")
    memory = MemoryStore()
    runtime = Runtime(adapter, workspace, memory)
    
    runtime.on(EventType.TOOL_CALL_START, lambda e: print(
        f"\n🔧 [{e.data['name']}] ...", end="", flush=True
    ))
    runtime.on(EventType.TOOL_CALL_END, lambda e: print(
        f" {'✅' if e.data['success'] else '❌'}"
    ))
    
    print("⚒️ Forge v0.2 — Transactional SE Runtime for LLMs")
    print("=" * 40)
    
    while True:
        try:
            user_input = input("\n💬 > ")
            if not user_input.strip():
                continue
            if user_input.strip().lower() in ("exit", "quit"):
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
