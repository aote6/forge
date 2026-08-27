#!/usr/bin/env python3
"""Forge 入口 - DeepSeek（付费主力）

支持环境变量：
  DEEPSEEK_API_KEY   必填
  DEEPSEEK_MODEL     可选，默认 deepseek-v4-flash
"""
import json
import os
import sys
import time
from pathlib import Path

from forge.workspace import Workspace
from forge.memory import MemoryStore
from forge.runtime import Runtime
from forge.events import EventType
from forge.adapters.deepseek import DeepSeekAdapter
from forge.tui_input import read_multiline_input
from forge.terminal_present import TerminalPresenter
from forge.terminal_color import ALARM, AMBER, PHOSPHOR, paint

project_root = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
adapter = DeepSeekAdapter(
    model_name=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
)
tag = "DeepSeek"


def _collect_health_notes(runtime: Runtime) -> list[str]:
    """检查工具失败率和最近测试状态，返回主动提醒列表。零 Token。"""
    notes = []
    try:
        history = runtime.executor.call_history if hasattr(runtime, "executor") else {}
        if history:
            total_entries = sum(len(v) for v in history.values())
            fail_entries = sum(
                1 for v in history.values() for s in v if s.startswith("fail")
            )
            if total_entries >= 3 and fail_entries / total_entries >= 0.35:
                notes.append(
                    f"我的工具最近失败率偏高（{fail_entries}/{total_entries}），可能有些工具接口需要修。"
                )
    except Exception:
        pass
    try:
        health_file = Path(runtime.workspace.project_root) / ".forge" / "health.json"
        if health_file.exists():
            health = json.loads(health_file.read_text(encoding="utf-8"))
            failed_tests = health.get("failed_tests", [])
            if failed_tests:
                first = failed_tests[0]
                notes.append(
                    f"刚才后台检查发现 {len(failed_tests)} 处测试失败，例如：{first}"
                )
    except Exception:
        pass
    return notes


_OFFLINE_HINT = (
    "veritasd 不在线：读/搜/规划/一般 shell 可用；"
    "事务写入主路径（str_replace/write_file 等）当前不可用。"
)


def _check_veritas(runtime: Runtime) -> None:
    """Non-blocking health check for veritasd. Copy only; no behavior change."""
    try:
        w = runtime.world
        if w is None:
            print(paint(f"WARN: {_OFFLINE_HINT}", AMBER))
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
            print(paint(f"WARN: {_OFFLINE_HINT}", AMBER))
            print("   需要事务写入时再启动 veritasd。")
        else:
            print(paint("OK: veritasd 在线 — 事务写入主路径可用", PHOSPHOR))
    except Exception as e:
        print(paint(f"WARN: veritasd 检查失败: {e}", AMBER))
        print(f"   {_OFFLINE_HINT}")


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
    """后台跑 pytest 和 TODO 扫描，结果写入 .forge/health.json。不阻塞 REPL。

    默认关闭（手机端费电/费时）。需要时：export FORGE_HEALTH_CHECK=1
    """
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
        print(f"🔌 使用 {tag} ({adapter.model_name})")
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

    print(f"🔌 使用 {tag} ({adapter.model_name})")
    print(f"📁 项目: {os.path.abspath(os.path.expanduser(project_root))}")
    workspace = Workspace(project_root=project_root)
    memory = MemoryStore()

    # Presenter must exist before cli_confirm so confirm can take exclusive terminal.
    presenter = TerminalPresenter()

    def cli_confirm(summary: str) -> bool:
        """CLI-owned confirmation: exclusive terminal until user answers.

        While active: heartbeat / tool / assistant presenter output is suppressed.
        Only this prompt may write; input is blocking and uncontested.
        """
        from forge.confirmation import is_cancel, is_confirm

        with presenter.exclusive_terminal():
            # Explicit newlines so the block is isolated from any prior tool line.
            print("\n── 子任务待确认的写操作 ──", flush=True)
            print(summary or "", flush=True)
            try:
                line = read_multiline_input("回复「确认」执行；「取消」拒绝 > ")
            except Exception:
                return False
            if line is None:
                return False
            if is_confirm(line):
                return True
            if is_cancel(line):
                return False
            return False

    runtime = Runtime(adapter, workspace, memory, confirm_provider=cli_confirm)
    _background_health_check(project_root)

    runtime.on(EventType.TOOL_CALL_START, presenter.on_tool_start)
    runtime.on(EventType.TOOL_CALL_END, presenter.on_tool_end)
    runtime._on_assistant_delta = presenter.on_assistant_delta
    runtime._on_assistant_done = presenter.on_assistant_done

    _check_veritas(runtime)
    _print_world_summary(runtime)

    print("⚒️ Forge | 工具循环 | 输入 q 退出")
    print("  工具输出会即时显示（last 看全文）；后台自检默认关 (FORGE_HEALTH_CHECK=1 开启)")
    print("=" * 40)

    while True:
        try:
            user_input = read_multiline_input("\n💬 > ")
            if user_input is None:  # EOF / Ctrl+D 空输入：退出
                user_input = "q"
            if not user_input.strip():
                continue
            cmd = user_input.strip().lower()
            if cmd in ("last", "copy", "clip"):
                disp = getattr(runtime, "_last_tool_display", None) or ""
                name = getattr(runtime, "_last_tool_name", "") or ""
                presenter.page_last(name, disp)
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
                    print(paint(f"\nWARN: [自检] {note}", AMBER))
            except Exception:
                pass
            response = runtime.run(user_input)
            n = getattr(runtime, "_last_tool_calls", 0)
            print(f"\n📊 本次工具调用: {n}")
            if response:
                presenter.show_assistant(
                    response,
                    force=getattr(runtime, "_last_response_needs_display", False),
                )
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
            print(paint(f"\nFAIL: 会话异常，为避免继续损坏状态已终止本次会话：{e}", ALARM))
            try:
                runtime.save_session_summary()
                _save_conversation_history(runtime)
            except Exception:
                pass
            break


if __name__ == "__main__":
    main()
