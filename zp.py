#!/usr/bin/env python3
"""Forge 入口 - 智谱 GLM（免费主力）"""
import json
import os
import sys
import time
from pathlib import Path

from forge.workspace import Workspace
from forge.memory import MemoryStore
from forge.runtime import Runtime
from forge.events import EventType
from forge.adapters.zhipu import ZhipuAdapter

project_root = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
# 免费 Flash 优先，之后可改成 glm-5.2 / glm-5.3（如果有免费额度）
adapter = ZhipuAdapter(model_name=os.environ.get("ZHIPU_MODEL", "glm-4.7-flash"))
tag = "智谱"


def _collect_health_notes(runtime: Runtime) -> list[str]:
    notes = []
    try:
        history = runtime.executor.call_history if hasattr(runtime, "executor") else {}
        if history:
            total_entries = sum(len(v) for v in history.values())
            fail_entries = sum(1 for v in history.values() for s in v if s.startswith("fail"))
            if total_entries >= 3 and fail_entries / total_entries >= 0.35:
                notes.append(f"我的工具最近失败率偏高（{fail_entries}/{total_entries}），可能有些工具接口需要修。")
    except Exception:
        pass
    try:
        health_file = Path(runtime.workspace.project_root) / ".forge" / "health.json"
        if health_file.exists():
            health = json.loads(health_file.read_text(encoding="utf-8"))
            failed_tests = health.get("failed_tests", [])
            if failed_tests:
                first = failed_tests[0]
                notes.append(f"刚才后台检查发现 {len(failed_tests)} 处测试失败，例如：{first}")
    except Exception:
        pass
    return notes


def _check_veritas(runtime: Runtime) -> None:
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
        print(f"⚠️ veritasd 检查失败: {e}\n   World 操作可能不可用。文件操作不受影响。")


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
    root = Path(runtime.workspace.project_root) / ".forge"
    root.mkdir(parents=True, exist_ok=True)
    msgs = []
    for m in runtime.conversation.get_messages():
        role = getattr(m, "role", None)
        content = getattr(m, "content", None) or ""
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content[:2000]})
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
    (root / "session_summary.json").write_text(
        json.dumps({"notes": replies}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _background_health_check(project_root: str) -> None:
    if os.environ.get("FORGE_HEALTH_CHECK", "").strip() not in ("1", "true", "yes", "on"):
        return
    import threading
    import subprocess

    def _run():
        result = {"failed_tests": [], "todo_count": 0, "checked_at": time.time()}
        try:
            r = subprocess.run(
                ["python3", "-m", "pytest", "tests/", "-q", "--maxfail=3"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if r.returncode != 0:
                lines = (r.stdout or "").strip().splitlines()
                failed = [l for l in lines if l.startswith("FAILED")]
                result["failed_tests"] = failed[:5]
        except Exception:
            pass
        try:
            for py_file in Path(project_root).rglob("*.py"):
                if ".git" in py_file.parts or "__pycache__" in py_file.parts:
                    continue
                txt = py_file.read_text(encoding="utf-8", errors="replace")
                result["todo_count"] += txt.count("TODO") + txt.count("FIXME")
        except Exception:
            pass
        try:
            health_dir = Path(project_root) / ".forge"
            health_dir.mkdir(parents=True, exist_ok=True)
            (health_dir / "health.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()


def main():
    if len(sys.argv) >= 3 and sys.argv[1] in ("sync", "status"):
        action = sys.argv[1]
        root = sys.argv[2]
        print(f"🔌 使用 {tag}")
        print(f"📁 项目: {os.path.abspath(os.path.expanduser(root))}")
        workspace = Workspace(project_root=root)
        memory = MemoryStore()
        runtime = Runtime(adapter, workspace, memory)
        try:
            report = runtime.sync_status() if action == "status" else runtime.sync()
            print(report.format())
        finally:
            try:
                runtime.world.close()
            except Exception:
                pass
        return

    print(f"🔌 使用 {tag}")
    print(f"📁 项目: {os.path.abspath(os.path.expanduser(project_root))}")
    workspace = Workspace(project_root=project_root)
    memory = MemoryStore()
    runtime = Runtime(adapter, workspace, memory)
    _background_health_check(project_root)

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

    _check_veritas(runtime)
    _print_world_summary(runtime)

    print("⚒️ Forge | 工具循环 | 输入 q 退出")
    print("  工具输出会即时显示（last 看全文）；后台自检默认关 (FORGE_HEALTH_CHECK=1 开启)")
    print("=" * 40)

    while True:
        try:
            user_input = input("\n💬 > ")
            if not user_input.strip():
                continue
            cmd = user_input.strip().lower()
            if cmd in ("last", "copy", "clip"):
                disp = getattr(runtime, "_last_tool_display", None) or "(no tool output yet)"
                name = getattr(runtime, "_last_tool_name", "")
                print(f"\n📋 last tool={name}\n{disp}")
                continue
            if cmd in ("changes",):
                tools = runtime.executor.tools
                if "session_changes" in tools:
                    print("\n" + tools["session_changes"]().display)
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
            try:
                notes = _collect_health_notes(runtime)
                for note in notes:
                    print(f"\n⚠️ [自检] {note}")
            except Exception:
                pass
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
