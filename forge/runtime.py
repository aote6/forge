"""Runtime — session shell for Forge.

Production path (唯一):
  Runtime.run(task) → _run_conversation() 工具循环
    → READ_ONLY + MUTATION schemas
    → ToolExecutor → IntentExecutor → Veritas commit/abort → Projection

run_legacy / AgentPhase 确认流已 DEPRECATED；生产只用 _run_conversation。
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Optional

from forge.adapters.base import BaseAdapter, Message, ToolResult
from forge.agent_state import AgentPhase
from forge.confirmation import is_cancel, is_confirm
from forge.conversation import Conversation
from forge.events import Event, EventType
from forge.memory import MemoryStore
from forge.projections.base import ProjectionManager
from forge.projections.file_projection import FileProjection
from forge.projections.git_projection import GitProjection
from forge.projections.index_projection import IndexProjection
from forge.system_prompt import SYSTEM_INSTRUCTION
from forge.tools import make_tools
from forge.tools.schemas import (
    TOOL_DECLARATIONS,
    READ_ONLY_TOOL_DECLARATIONS,
    MUTATION_TOOL_DECLARATIONS,
    MUTATION_TOOL_NAMES,
    SUBMIT_PLAN_TOOL_NAME,
    SUBMIT_PLAN_DECLARATION,
)
from forge.workspace import Workspace
from forge.world import WorldRuntime

MAX_AGENT_STEPS = 40
MAX_CONSECUTIVE_FAILURES = 3

# 规划/执行阶段注入到 system 的额外指令
_PLANNING_INSTRUCTION = """
## 当前阶段：规划（只读）
你现在只有只读/查询工具，无法修改代码。需要改动时：先只读探索定位，
然后调用 submit_plan 提交计划并停下等待确认。纯问答直接回答即可。
"""
_EXECUTION_INSTRUCTION = """
## 当前阶段：执行
用户已确认以下计划，请按计划执行修改：
{plan}

执行中若发现计划需要偏离或推翻，先停下说明，不要擅自大改。
"""
_PLAN_CONFIRM_PROMPT = (
    "\n\n── 以上是计划 ──\n"
    "回复「确认」开始执行；「取消」放弃；或直接说出你的修改意见。"
)

# 确认词 + 后续分隔符：用于把「确认，另外改下 b.py」拆成「确认」+「补充意见」，
# 避免把用户确认时顺带说的补充意见丢掉。
_CONFIRM_PREFIX_RE = re.compile(
    r"^(?:确认|confirm|commit|ok|yes|y|执行|go)\b\s*[，,。.、:：\s]*",
    re.IGNORECASE,
)


def _strip_confirm_prefix(text: str) -> str:
    """去掉开头的确认词，返回剩余补充意见（裸确认返回空串）。"""
    return _CONFIRM_PREFIX_RE.sub("", (text or "").strip(), count=1).strip()



def _append_conversation_log(project_root: str, role: str, content: str, **extra) -> None:
    """Append one JSONL line to .forge/conversation_log.jsonl for search_history."""
    import json
    import time
    from pathlib import Path as _P
    try:
        log_dir = _P(project_root) / ".forge"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "conversation_log.jsonl"
        rec = {"ts": time.time(), "role": role, "content": (content or "")[:4000], **extra}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[forge] _append_conversation_log failed: {e}", file=sys.stderr)



def _load_session_summary(project_root: str) -> str:
    """Load prior session summary / conversation history for system prompt."""
    from pathlib import Path as _P
    root = _P(project_root) / ".forge"
    notes = []
    tasks = []
    try:
        hist = root / "conversation_history.json"
        if hist.is_file():
            data = json.loads(hist.read_text(encoding="utf-8"))
            notes = list(data.get("notes") or [])
            summary = data.get("summary") or {}
            tasks = list(summary.get("last_tasks") or [])
            if not notes:
                notes = list(summary.get("last_conclusions") or [])
        path = root / "session_summary.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            notes = notes or list(data.get("notes") or data.get("summaries") or [])
    except Exception:
        return ""
    if not notes and not tasks:
        return ""
    parts = ["\n\n## 上次会话摘要"]
    if tasks:
        parts.append("任务:")
        parts.extend(f"- {t}" for t in tasks[-3:])
    if notes:
        parts.append("结论:")
        parts.extend(f"- {n[:300]}" for n in notes[-5:])
    return "\n".join(parts)
def _save_session_summary(project_root: str, assistant_replies: list[str]) -> None:
    from pathlib import Path as _P
    try:
        log_dir = _P(project_root) / ".forge"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "session_summary.json"
        notes = [r.strip()[:500] for r in assistant_replies if r and r.strip()][-5:]
        path.write_text(
            json.dumps({"notes": notes}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


# 结果确认型工具：第一行就是精华（RESULT: path=... tx=... 之类），压成一行安全。
# 其余(默认)按"内容承载型"处理：read_file/str_replace/near_miss/diff 等，
# 精华常常不在第一行，压缩过头是长会话后期质量下滑的直接原因之一。
# NOTE: 这份名单是从代码里认出的工具名猜的，请对照 forge/tools/schemas.py
# 里的实际工具名核对一遍，缺漏的往"内容型"（默认分支）方向偏，不要往
# "确认型"偏——宁可少压缩，不要错压缩。
_CONFIRMATION_TOOLS = {
    "write_file", "modify_file", "undo_last_tx", "create_object",
    "delete_file", "create_file", "unlink_objects", "link_objects",
    "run_test_structured", "apply_patch", "edit_files_batch", "todo_write",
}


def _compress_messages(messages: list, keep_recent_tools: int = 6) -> list:
    """Replace older tool results with summaries to curb context rot.

    结果确认型工具压成一行；内容承载型工具保留更大预算（多行+字符上限），
    避免第20步之后模型"以为看过"实则早被压没了的内容。
    """
    if len(messages) < 24:
        return messages
    tool_idxs = [i for i, m in enumerate(messages) if getattr(m, "role", None) == "tool"]
    if len(tool_idxs) <= keep_recent_tools:
        return messages
    drop = set(tool_idxs[:-keep_recent_tools])
    out = []
    for i, m in enumerate(messages):
        if i in drop:
            name = getattr(m, "name", None) or "tool"
            content = (getattr(m, "content", None) or "")
            stripped = content.strip()
            if not stripped:
                summary = f"[compressed FACT {name}] "
            elif name in _CONFIRMATION_TOOLS:
                first = stripped.splitlines()[0][:120]
                summary = f"[compressed FACT {name}] {first}"
            else:
                lines = stripped.splitlines()
                kept = lines[:8]
                body = "\n".join(kept)[:800]
                truncated = len(lines) > 8 or len(body) < len(stripped)
                more = (
                    f"\n...[截断，原始长度 {len(stripped)} 字符/{len(lines)} 行]"
                    if truncated else ""
                )
                summary = f"[compressed {name}]\n{body}{more}"
            try:
                from forge.adapters.base import Message as ForgeMessage
                out.append(ForgeMessage(role="tool", content=summary, tool_call_id=getattr(m, "tool_call_id", None), name=name))
            except Exception:
                out.append(m)
        else:
            out.append(m)
    return out


def _todo_nudge_from_tools(tools: dict) -> str:
    """If todo_list exists and has pending items, return a short reminder."""
    fn = tools.get("todo_list")
    if not fn:
        return ""
    try:
        r = fn()
        items = (r.payload or {}).get("todos") or []
        pending = [it for it in items if it.get("status") in ("pending", "in_progress")]
        if not pending:
            return ""
        lines = [f"- [{it.get('status')}] {it.get('content')}" for it in pending[:7]]
        return "\n[system reminder] 未完成 todo（以用户最新消息为准）:\n" + "\n".join(lines)
    except Exception:
        return ""


class ToolExecutor:
    def __init__(self, tools: dict):
        self.tools = tools
        self.call_history: dict[str, list[str]] = {}

    def _args_signature(self, tool_name: str, arguments: dict) -> str:
        """Signature for retry circuit-breaker.

        str_replace: tool+path+hash(old_string) so changing old_string is a fresh attempt.
        Other tools: full args JSON.
        """
        args = arguments or {}
        if tool_name == "str_replace":
            import hashlib
            path = str(args.get("path") or "")
            old = str(args.get("old_string") or "")
            h = hashlib.sha1(old.encode("utf-8", errors="replace")).hexdigest()[:12]
            return f"str_replace:{path}:{h}"
        if tool_name == "write_file":
            import hashlib
            path = str(args.get("path") or "")
            content = str(args.get("content") or "")
            h = hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()[:12]
            return f"write_file:{path}:{h}"
        if tool_name == "modify_file":
            import hashlib
            path = str(args.get("path") or "")
            ops = args.get("operations") or []
            ops_json = json.dumps(ops, sort_keys=True, ensure_ascii=False, default=str)
            h = hashlib.sha1(ops_json.encode("utf-8", errors="replace")).hexdigest()[:12]
            return f"modify_file:{path}:{h}"
        return tool_name + ":" + json.dumps(
            args, sort_keys=True, ensure_ascii=False, default=str
        )

    def reset(self):
        self.call_history.clear()

    def execute(self, tool_call) -> ToolResult:
        # All tools executable directly. Mutation tools internally handle
        # transaction begin/commit/abort via IntentExecutor + WorldSession.
        if tool_call.name in MUTATION_TOOL_NAMES:
            pass  # mutation tools allowed — they handle commit/abort internally
        fn = self.tools.get(tool_call.name)
        if not fn:
            return ToolResult.fail(display=f"未知工具: {tool_call.name}")

        sig = self._args_signature(tool_call.name, tool_call.arguments)
        history = self.call_history.get(sig, [])

        consecutive_failures = 0
        last_kind = ""
        for s in reversed(history):
            if s.startswith("fail"):
                consecutive_failures += 1
                if not last_kind and ":" in s:
                    last_kind = s.split(":", 1)[1]
            else:
                break

        _KIND_ADVICE = {
            "type_mismatch": "参数结构反复不对，重新读一遍工具schema再改参数，不要靠猜。",
            "exception": "运行时异常反复出现，问题可能不在参数上，检查前置状态(文件是否存在/veritasd是否在线)。",
            "logic": "工具正常执行但业务上判定失败(如old_string未找到)，仔细核对返回里的HINT/NEAR_MISS。",
        }

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            advice = _KIND_ADVICE.get(last_kind, "请换策略、read 核对，或直接问用户。")
            return ToolResult.fail(
                display=(
                    f"STOP_HINT: 同一调用已连续失败 {consecutive_failures} 次(原因: {last_kind or '未知'})，已禁止再试。\n"
                    f"  {tool_call.name}({json.dumps(tool_call.arguments, ensure_ascii=False)})\n"
                    f"{advice} 不要继续微调同一参数。"
                )
            )

        try:
            result = fn(**tool_call.arguments)
            status = "success" if result.success else "fail:logic"
            self.call_history.setdefault(sig, []).append(status)
            if not result.success and consecutive_failures >= 1:
                # after 1 prior fail, this is 2nd+ failure in a row for same sig
                prefix = (
                    f"STOP_HINT: 该调用已连续失败 {consecutive_failures + 1} 次(原因: logic)。"
                    f"请换方向或问用户，勿重复同一操作。\n"
                )
                if result.display and "STOP_HINT" not in result.display:
                    result.display = prefix + result.display
            return result
        except TypeError as e:
            self.call_history.setdefault(sig, []).append("fail:type_mismatch")
            return ToolResult.fail(
                display=f"参数不匹配: {e}\n收到的参数: {tool_call.arguments}"
            )
        except Exception as e:
            self.call_history.setdefault(sig, []).append("fail:exception")
            return ToolResult.fail(display=f"工具执行异常: {type(e).__name__}: {e}")


class Runtime:
    def __init__(self, adapter: BaseAdapter, workspace: Workspace, memory: MemoryStore):
        self.adapter = adapter
        self.workspace = workspace
        self.memory = memory
        self.world = WorldRuntime(project_root=workspace.project_root)
        # Identity 是 mutation / session 的前置条件；失败则 Runtime 不得以正常状态启动。
        try:
            self.world.ensure_identity()
        except Exception as e:
            raise RuntimeError(
                f"Forge Runtime 启动失败：无法建立 World identity: {e}"
            ) from e

        from forge.sync.state import SyncState
        from forge.sync.sync_layer import SyncLayer

        # Sync metadata 权威状态（决策 1：.forge/sync_state.json，不放入 Veritas）。
        self.sync_state = SyncState(project_root=workspace.project_root)
        path_map = getattr(self.world, "_path_map", None)
        file_projection = FileProjection(
            project_root=workspace.project_root,
            object_path_map=path_map,
            sync_state=self.sync_state,
        )
        self.projections = ProjectionManager(
            checkpoint_dir=os.path.join(workspace.project_root, ".forge")
        )
        self.projections.register(file_projection)
        self.projections.register(GitProjection(project_root=workspace.project_root))
        self.projections.register(IndexProjection(project_root=workspace.project_root))

        self.sync_layer = SyncLayer(
            project_root=workspace.project_root,
            world_runtime=self.world,
            sync_state=self.sync_state,
            file_projection=file_projection,
        )

        try:
            self._startup_sync_check()
        except Exception as e:
            print(f"[sync] startup check error: {e}", file=sys.stderr)

        # Single tool-loop: all tools available (read + mutation).
        # Mutations go through IntentExecutor → WorldSession → Veritas,
        # with commit/abort handled inside the tool call.
        tools, confirm_fn, abort_fn = make_tools(
            workspace=workspace,
            world_runtime=self.world,
            projections=self.projections,
            allow_mutation=True,
            sync_layer=self.sync_layer,
        )
        def spawn_subagent(task: str, max_steps: int = 15) -> ToolResult:
            """Run an isolated subagent tool-loop; return conclusion text only."""
            from forge.subagent import run_subagent
            from forge.tools.schemas import MUTATION_TOOL_DECLARATIONS

            try:
                schemas = list(READ_ONLY_TOOL_DECLARATIONS) + list(MUTATION_TOOL_DECLARATIONS)
                conclusion = run_subagent(
                    self.adapter,
                    tools,
                    schemas,
                    task,
                    max_steps=int(max_steps) if max_steps else 15,
                )
                return ToolResult.ok(
                    display="RESULT: subagent_done\n" + (conclusion or ""),
                    payload={"conclusion": conclusion, "subagent": True},
                )
            except Exception as e:
                return ToolResult.fail(
                    display=(
                        "spawn_subagent failed: "
                        + str(e)
                        + "\n建议: 缩小子任务范围；确认模型与 veritasd 可用。"
                    )
                )

        tools["spawn_subagent"] = spawn_subagent
        self.executor = ToolExecutor(tools)
        self._confirm_fn = confirm_fn
        self._abort_fn = abort_fn

        self.conversation = Conversation()
        self.conversation.append(Message(role="system", content=SYSTEM_INSTRUCTION))
        self.phase = AgentPhase.IDLE
        self._handlers: dict = {t: [] for t in EventType}
        # 规划→确认→执行 状态：待用户确认的计划与原任务
        self._pending_plan: str | None = None
        self._pending_task: str | None = None
        self._submitted_plan: str | None = None

    def _startup_sync_check(self):
        """启动时只做同步状态检测，不自动 replay receipt 写磁盘（决策 3/8）。

        契约 §4：发现分叉则 STOP；发现 FAST_FORWARD 也不自动推进，
        仅提示显式 forge_sync。
        """
        from forge.recovery.check import RecoveryCheck
        from forge.sync.sync_layer import (
            CONFLICT,
            FAST_FORWARD_DISK_TO_WORLD,
            FAST_FORWARD_WORLD_TO_DISK,
            IN_SYNC,
            NOT_A_GIT_REPO,
        )

        report = RecoveryCheck(self.sync_layer).check()
        status = report.status
        if status == IN_SYNC:
            return
        if status == NOT_A_GIT_REPO:
            print("[sync] 工作区不是 Git 仓库；跳过同步状态检测。", file=sys.stderr)
            return
        if status == CONFLICT:
            print(
                "[sync] CONFLICT：World 与 Disk/Git 在共同已知状态之后都发生了独立变化。\n"
                "       已停止自动同步；不覆盖磁盘、不覆盖 World、不推进水位。\n"
                "       请运行 forge_sync 查看 diff 并显式决策。",
                file=sys.stderr,
            )
            return
        direction = (
            "World → Disk"
            if status == FAST_FORWARD_WORLD_TO_DISK
            else "Disk → World"
        )
        print(
            f"[sync] 检测到 FAST_FORWARD({direction})；启动时不自动推进。\n"
            f"       请运行 forge_sync 执行显式同步。",
            file=sys.stderr,
        )

    def sync_status(self):
        """程序化同步状态查询（返回 SyncReport）。"""
        return self.sync_layer.detect()

    def sync(self):
        """显式执行 `forge sync`：检测 → 依状态安全推进 / 报告冲突。"""
        return self.sync_layer.sync()

    def _guard_external_change(self, tool_name: str):
        """运行期间外部变更守卫：变更工具执行前检测外部磁盘/Git 变化。

        契约 §7：持锁写入期间发现外部磁盘变化 → 立即停止继续写入，重新对账。
        World 不可达时同样 STOP：Forge 写盘不会产生 World receipt，
        会产生无痕分叉，必须先恢复 veritasd。
        """
        if tool_name not in MUTATION_TOOL_NAMES:
            return None
        # forge_sync 是对账入口本身，不得被外部变更守卫拦截（否则无法解决冲突）。
        if tool_name == "forge_sync":
            return None
        try:
            if self.sync_layer is not None:
                if not self.sync_layer.world_available():
                    from forge.adapters.base import ToolResult
                    return ToolResult.fail(
                        display=(
                            "⛔ 无法访问 World（veritasd）：已停止继续写入。\n"
                            "Forge 写盘会产生 World 无法记录的变化，禁止在不可达时继续。\n"
                            "请先恢复 veritasd 后重试。"
                        )
                    )
                if self.sync_layer.external_change_detected():
                    from forge.adapters.base import ToolResult
                    return ToolResult.fail(
                        display=(
                            "⛔ 检测到外部磁盘/Git 变化：已停止继续写入。\n"
                            "请先运行 forge_sync 重新对账，确认同步状态后再继续编辑。"
                        )
                    )
        except Exception as e:
            import sys
            print(f"[sync] external-change guard failed: {e}", file=sys.stderr)
        return None
    def on(self, event_type: EventType, handler):
        self._handlers[event_type].append(handler)

    def emit(self, event: Event) -> Event:
        for handler in self._handlers.get(event.type, []):
            handler(event)
            if event.cancelled:
                break
        return event

    def run(self, task: str, task_id: str | None = None) -> str:
        """Single path: 规划(只读) → 用户确认 → 执行(mutation)。

        默认先进入规划阶段：只读工具 + submit_plan。模型要改代码时必须
        先 submit_plan，运行时把计划交还用户确认；确认后下一轮才放行
        mutation 工具执行。纯问答直接返回，不需要确认。
        """
        self._last_tool_calls = 0
        self._last_assistant_replies = []

        # 有待确认的计划：先处理用户对计划的回复
        if self._pending_plan is not None:
            result = self._handle_plan_reply(task)
        else:
            result = self._run_planning(task)

        n = getattr(self, "_last_tool_calls", 0)
        print(f"[stats] tools={n}", file=sys.stderr)
        if result is None:
            return "(no response)"
        return result if isinstance(result, str) else str(result)

    def _handle_plan_reply(self, reply: str) -> str:
        """用户对计划回复：确认→执行；取消→放弃；其它→当作补充意见重新规划。"""
        if is_confirm(reply):
            plan = self._pending_plan
            task = self._pending_task
            self._pending_plan = None
            self._pending_task = None
            extra = _strip_confirm_prefix(reply)
            if extra:
                task = (task or "") + "\n（用户确认时的补充）" + extra
            return self._run_execution(task, plan)
        if is_cancel(reply):
            self._pending_plan = None
            self._pending_task = None
            return "已取消，未做任何改动。"
        # 其余内容：用户对计划有意见/新指令，并入原任务重新规划
        original = self._pending_task or ""
        self._pending_plan = None
        self._pending_task = None
        combined = original + "\n（用户对计划的补充/修正）" + reply
        return self._run_planning(combined)

    def _run_planning(self, task: str) -> str:
        """规划阶段：只读工具 + submit_plan。返回计划（待确认）或直接答案。"""
        self._submitted_plan = None
        result = self._run_conversation(
            task,
            schemas=list(READ_ONLY_TOOL_DECLARATIONS) + [SUBMIT_PLAN_DECLARATION],
            extra_system=_PLANNING_INSTRUCTION,
        )
        if self._submitted_plan:
            self._pending_plan = self._submitted_plan
            self._pending_task = task
            return result + _PLAN_CONFIRM_PROMPT
        return result

    def _run_execution(self, task: str, plan: str) -> str:
        """执行阶段：用户已确认计划，放行 mutation 工具按计划执行。"""
        self._submitted_plan = None
        return self._run_conversation(
            task,
            schemas=list(READ_ONLY_TOOL_DECLARATIONS)
            + list(MUTATION_TOOL_DECLARATIONS),
            extra_system=_EXECUTION_INSTRUCTION.format(plan=plan),
        )

    def save_session_summary(self) -> None:
        """Persist last assistant replies for next process start."""
        replies = getattr(self, "_last_assistant_replies", None) or []
        # also pull from conversation
        for m in self.conversation.get_messages():
            if getattr(m, "role", None) == "assistant" and getattr(m, "content", None):
                replies.append(m.content)
        _save_session_summary(self.workspace.project_root, replies)

    def _run_conversation(self, task: str, schemas: list, extra_system: str = "") -> str:
        """Tool-calling loop；schemas 决定本轮可见工具（规划=只读，执行=只读+mutation）。"""
        from forge.adapters.base import Message as ForgeMessage

        prior = _load_session_summary(self.workspace.project_root)
        try:
            from forge.tools.project_memory import format_for_prompt
            mem = format_for_prompt(self.workspace.project_root)
        except Exception:
            mem = ""
        system = SYSTEM_INSTRUCTION + (extra_system or "") + (prior or "") + (mem or "")
        messages = [ForgeMessage(role="system", content=system)]
        history = self.conversation.get_messages()
        if history:
            recent = [m for m in history if m.role != "system"][-20:]
            messages.extend(recent)
        messages.append(ForgeMessage(role="user", content=task))
        _append_conversation_log(self.workspace.project_root, "user", task)
        try:
            from forge.tools.goal_clarify import (
                needs_clarify,
                clarification_message,
                user_looks_like_clarification,
                mark_clarified,
            )
            if user_looks_like_clarification(task):
                mark_clarified()
            elif needs_clarify(task):
                messages.append(ForgeMessage(role="user", content=clarification_message()))
        except Exception as e:
            import sys
            print(f"[forge] goal_clarify unavailable: {e}", file=sys.stderr)

        tool_calls_n = 0
        assistant_replies: list[str] = []
        half = max(5, MAX_AGENT_STEPS // 2)
        nudged = False
        for step_i in range(MAX_AGENT_STEPS):
            messages = _compress_messages(messages)
            if step_i >= half and not nudged:
                nudge = _todo_nudge_from_tools(self.executor.tools)
                if nudge:
                    messages.append(ForgeMessage(role="user", content=nudge))
                    nudged = True
            resp = self.adapter.send(messages, schemas)
            if not resp.tool_calls:
                if resp.content:
                    self.conversation.append(ForgeMessage(role="user", content=task))
                    self.conversation.append(ForgeMessage(role="assistant", content=resp.content))
                    _append_conversation_log(
                        self.workspace.project_root, "assistant", resp.content or ""
                    )
                    assistant_replies.append(resp.content)
                self._last_tool_calls = tool_calls_n
                self._last_assistant_replies = assistant_replies
                return resp.content or "(no response)"
            messages.append(ForgeMessage(
                role="assistant",
                content=resp.content,
                tool_calls=resp.tool_calls
            ))
            if resp.content:
                assistant_replies.append(resp.content)
            for tc in resp.tool_calls:
                if tc.name == SUBMIT_PLAN_TOOL_NAME:
                    # 模型提交计划 → 中断本轮，交还用户确认，不放行 mutation。
                    plan = (tc.arguments or {}).get("plan") or (resp.content or "")
                    self._submitted_plan = plan
                    self.conversation.append(ForgeMessage(role="user", content=task))
                    self.conversation.append(ForgeMessage(role="assistant", content=plan))
                    _append_conversation_log(
                        self.workspace.project_root, "assistant", plan or ""
                    )
                    assistant_replies.append(plan)
                    self._last_tool_calls = tool_calls_n
                    self._last_assistant_replies = assistant_replies
                    return plan or "(no plan)"
                self.emit(
                    Event(EventType.TOOL_CALL_START, {"name": tc.name, "args": tc.arguments})
                )
                guard = self._guard_external_change(tc.name)
                result = guard if guard is not None else self.executor.execute(tc)
                tool_calls_n += 1
                self._last_tool_display = result.display or ""
                self._last_tool_name = tc.name
                self.emit(
                    Event(
                        EventType.TOOL_CALL_END,
                        {
                            "name": tc.name,
                            "success": result.success,
                            "display": result.display,
                        },
                    )
                )

                messages.append(ForgeMessage(
                    role="tool",
                    content=result.display,
                    tool_call_id=tc.id,
                    name=tc.name
                ))
                _append_conversation_log(
                    self.workspace.project_root,
                    "tool",
                    result.display or "",
                    name=tc.name,
                    success=bool(result.success),
                )
        self._last_tool_calls = tool_calls_n
        self._last_assistant_replies = assistant_replies
        return "(达到最大工具调用次数)"

    # Backward-compat alias
    run_v2 = run

    def _update_phase_from_result(self, result: ToolResult):
        if not result.success or not result.payload:
            return
        payload = result.payload
        if payload.get("requires_confirmation"):
            self.phase = AgentPhase.WAIT_CONFIRM
            return
        phase_hint = payload.get("phase")
        if phase_hint == "verifying" and self.phase == AgentPhase.VERIFYING:
            self.phase = AgentPhase.REPORT
            return
        if payload.get("mutation") and not payload.get("requires_confirmation"):
            self.phase = AgentPhase.VERIFYING
            return
        if self.phase in (AgentPhase.IDLE, AgentPhase.DISCOVERY):
            self.phase = AgentPhase.DISCOVERY

    def run_legacy(self, user_input: str) -> str:
        """DEPRECATED interactive tool-loop (只读/确认兜底).

        Must not be used for engineering mutation tasks.
        Production path is Runtime.run → _run_conversation (full tool-loop).
        """
        if self.phase == AgentPhase.WAIT_CONFIRM:
            if is_confirm(user_input):
                if self._confirm_fn is None:
                    return "无法提交：确认回调未就绪。"
                result = self._confirm_fn()
                if result.success:
                    self.phase = AgentPhase.VERIFYING
                    self.conversation.append(
                        Message(
                            role="system",
                            content="事务已提交。现在进入验证阶段，请运行 git_diff 和测试。",
                        )
                    )
                return result.display
            if is_cancel(user_input):
                if self._abort_fn is not None:
                    result = self._abort_fn()
                    self.phase = AgentPhase.IDLE
                    return result.display
                self.phase = AgentPhase.IDLE
                return "事务已取消。"
            return "⏸️ 当前有待确认的事务\n请输入「确认」提交，或「取消」放弃。\n"

        self.executor.reset()
        if self.phase == AgentPhase.IDLE:
            self.phase = AgentPhase.DISCOVERY

        event = self.emit(Event(EventType.USER_MESSAGE, {"content": user_input}))
        if event.cancelled:
            return "⏸️ 已拦截。"

        self.conversation.append(Message(role="user", content=user_input))
        response = self.adapter.send(self.conversation.get_messages(), READ_ONLY_TOOL_DECLARATIONS)

        step_count = 0
        while response.tool_calls:
            step_count += 1
            if step_count > MAX_AGENT_STEPS:
                self.conversation.append(
                    Message(
                        role="assistant",
                        content=f"⛔ 已达到最大执行步数({MAX_AGENT_STEPS})，任务中止。",
                    )
                )
                return f"⛔ Agent 步数超限({MAX_AGENT_STEPS})，已强制终止。"

            self.conversation.append(
                Message(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )

            for tc in response.tool_calls:
                if self.phase == AgentPhase.WAIT_CONFIRM:
                    self.conversation.append(
                        Message(
                            role="tool",
                            content="⏸️ 当前有未确认的事务，请等待用户确认后再继续。",
                            tool_call_id=tc.id,
                            name=tc.name,
                        )
                    )
                    continue

                self.emit(
                    Event(EventType.TOOL_CALL_START, {"name": tc.name, "args": tc.arguments})
                )
                result = self.executor.execute(tc)
                self.emit(
                    Event(
                        EventType.TOOL_CALL_END,
                        {"name": tc.name, "success": result.success, "display": result.display},
                    )
                )
                self.conversation.append(
                    Message(
                        role="tool",
                        content=result.display,
                        tool_call_id=tc.id,
                        name=tc.name,
                    )
                )
                self._update_phase_from_result(result)

                if (
                    result.success
                    and result.payload
                    and result.payload.get("requires_confirmation")
                ):
                    self.conversation.append(
                        Message(
                            role="system",
                            content="修改已准备完成。必须等待用户确认后才能继续。不要调用其它修改工具。",
                        )
                    )
                    return result.display

            response = self.adapter.send(
                self.conversation.get_messages(), READ_ONLY_TOOL_DECLARATIONS
            )

        if self.phase != AgentPhase.DONE:
            self.phase = AgentPhase.DONE
        self.emit(Event(EventType.ASSISTANT_REPLY, {"content": response.content or ""}))
        if response.content:
            self.conversation.append(Message(role="assistant", content=response.content))
        return response.content or ""
