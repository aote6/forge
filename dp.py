#!/usr/bin/env python3
"""Forge 入口 - 强制 DeepSeek；生产路径 = Runtime 工具循环。"""
import json
import os
import sys
from pathlib import Path

from forge.workspace import Workspace
from forge.memory import MemoryStore
from forge.runtime import Runtime
from forge.events import EventType
from forge.adapters.deepseek import DeepSeekAdapter

project_root = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
adapter = DeepSeekAdapter(model_name="deepseek-v4-flash")
tag = "DeepSeek"


def _check_veritas(runtime: Runtime) -> None:
    """Non-blocking health check for veritasd."""
    try:
        w = runtime.world
        if w is None:
            print("⚠️ veritasd 不在线（无 WorldRuntime）。World 操作不可用，文件操作不受影响。")
            return
        online = getattr(w, "online", None)
        if callable(online):
            ok = bool(online())
        elif isinstance(online, bool):
            ok = online
        else:
            # try a cheap world_info tool
            tools = runtime.executor.tools
            if "world_info" in tools:
                r = tools["world_info"]()
                ok = bool(r.success)
            else:
                ok = True
        if not ok:
            print(
                "⚠️ veritasd 不在线。World 操作（create_object / link_objects）不可用。\n"
                "   文件操作不受影响。启动 veritasd 或忽略此警告。"
            )
        else:
            print("✅ veritasd 在线")
    except Exception as e:
        print(
            f"⚠️ veritasd 检查失败: {e}\n"
            "   World 操作可能不可用。文件操作不受影响。"
        )


def _print_world_summary(runtime: Runtime) -> None:
    try:
        tools = runtime.executor.tools
        if "world_info" in tools:
            r = tools["world_info"]()
            if r.success:
                print(f"🌍 {r.display.split(chr(10))[0][:120]}")
                return
    except Exception as e:
        print(f"🌍 世界: (不可用: {e})")


def _save_conversation_history(runtime: Runtime) -> None:
    """Persist last turns + summary under .forge/."""
    root = Path(runtime.workspace.project_root) / ".forge"
    root.mkdir(parents=True, exist_ok=True)
    msgs = []
    for m in runtime.conversation.get_messages():
        role = getattr(m, "role", None)
        content = getattr(m, "content", None) or ""
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content[:2000]})
    # keep last 20 turns
    msgs = msgs[-20:]
    replies = [m["content"] for m in msgs if m["role"] == "assistant"][-5:]
    history = {
        "messages": msgs,
        "notes": replies,
        "summary": {
            "last_tasks": [m["content"][:200] for m in msgs if m["role"] == "user"][-3:],
            "last_conclusions": replies,
        },
    }
    (root / "conversation_history.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # also write session_summary for runtime injection
    (root / "session_summary.json").write_text(
        json.dumps({"notes": replies}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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

    _check_veritas(runtime)
    _print_world_summary(runtime)

    print("⚒️ Forge | 工具循环 | 输入 q 退出")
    print("=" * 40)

    while True:
        try:
            user_input = input("\n💬 > ")
            if not user_input.strip():
                continue
            if user_input.strip().lower() in ("exit", "quit", "q"):
                try:
                    runtime.save_session_summary()
                    _save_conversation_history(runtime)
                    print("💾 已保存会话历史 (.forge/conversation_history.json)")
                except Exception as e:
                    print(f"💾 保存失败: {e}")
                print("👋")
                break
            response = runtime.run(user_input)
            n = getattr(runtime, "_last_tool_calls", 0)
            print(f"\n📊 本次工具调用: {n}")
            if response:
                print(f"\n🤖 {response}")
        except KeyboardInterrupt:
            try:
                runtime.save_session_summary()
                _save_conversation_history(runtime)
            except Exception:
                pass
            print("\n👋")
            break
        except Exception as e:
            print(f"\n❌ {e}")


if __name__ == "__main__":
    main()
