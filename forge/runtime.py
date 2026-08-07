"""Runtime — session shell for Forge.

Owns: user session, conversation, tool loop (v1), event bus.
Does NOT own engineering phase logic — that lives in EngineeringOrchestrator.
"""
from __future__ import annotations

import json
import sys
from typing import Optional

from forge.adapters.base import BaseAdapter, Message, ToolResult
from forge.agent_state import AgentPhase
from forge.confirmation import is_cancel, is_confirm
from forge.conversation import Conversation
from forge.events import Event, EventType
from forge.memory import MemoryStore
from forge.memory.checkpoint import CheckpointStore
from forge.orchestrator.engine import EngineeringOrchestrator
from forge.planner import Planner
from forge.projections.base import ProjectionManager
from forge.projections.file_projection import FileProjection
from forge.projections.git_projection import GitProjection
from forge.projections.index_projection import IndexProjection
from forge.system_prompt import SYSTEM_INSTRUCTION
from forge.tools import make_tools
from forge.tools.schemas import TOOL_DECLARATIONS
from forge.workspace import Workspace
from forge.world import WorldRuntime

MAX_AGENT_STEPS = 20
MAX_CONSECUTIVE_FAILURES = 3


class ToolExecutor:
    def __init__(self, tools: dict):
        self.tools = tools
        self.call_history: dict[str, list[str]] = {}

    def _args_signature(self, tool_name: str, arguments: dict) -> str:
        return tool_name + ":" + json.dumps(
            arguments, sort_keys=True, ensure_ascii=False, default=str
        )

    def reset(self):
        self.call_history.clear()

    def execute(self, tool_call) -> ToolResult:
        fn = self.tools.get(tool_call.name)
        if not fn:
            return ToolResult.fail(display=f"未知工具: {tool_call.name}")

        sig = self._args_signature(tool_call.name, tool_call.arguments)
        history = self.call_history.get(sig, [])

        consecutive_failures = 0
        for s in reversed(history):
            if s == "fail":
                consecutive_failures += 1
            else:
                break

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            return ToolResult.fail(
                display=(
                    f"⛔ 该工具调用已连续失败 {consecutive_failures} 次，已禁止重试:\n"
                    f"  {tool_call.name}({json.dumps(tool_call.arguments, ensure_ascii=False)})\n"
                    f"💡 请换一种方式或告知用户遇到的问题。"
                )
            )

        try:
            result = fn(**tool_call.arguments)
            status = "success" if result.success else "fail"
            self.call_history.setdefault(sig, []).append(status)
            return result
        except TypeError as e:
            self.call_history.setdefault(sig, []).append("fail")
            return ToolResult.fail(
                display=f"参数不匹配: {e}\n收到的参数: {tool_call.arguments}"
            )
        except Exception as e:
            self.call_history.setdefault(sig, []).append("fail")
            return ToolResult.fail(display=f"工具执行异常: {type(e).__name__}: {e}")


class Runtime:
    def __init__(self, adapter: BaseAdapter, workspace: Workspace, memory: MemoryStore):
        self.adapter = adapter
        self.workspace = workspace
        self.memory = memory
        self.world = WorldRuntime(project_root=workspace.project_root)
        try:
            self.world.ensure_identity()
        except Exception:
            pass

        self.projections = ProjectionManager()
        path_map = getattr(self.world, "_path_map", None)
        self.projections.register(
            FileProjection(project_root=workspace.project_root, object_path_map=path_map)
        )
        self.projections.register(GitProjection(project_root=workspace.project_root))
        self.projections.register(IndexProjection(project_root=workspace.project_root))

        try:
            self._recover_projections()
        except Exception as e:
            print(f"[recovery] error: {e}", file=sys.stderr)

        tools, confirm_fn, abort_fn = make_tools(
            workspace=workspace,
            world=self.world,
            projections=self.projections,
        )
        self.executor = ToolExecutor(tools)
        self._confirm_fn = confirm_fn
        self._abort_fn = abort_fn

        self.conversation = Conversation()
        self.conversation.append(Message(role="system", content=SYSTEM_INSTRUCTION))
        self.phase = AgentPhase.IDLE
        self._handlers: dict = {t: [] for t in EventType}
        self._task_memory = CheckpointStore(workspace.project_root)
        self._planner = Planner(adapter)

    def _recover_projections(self):
        from forge.recovery.replay import ProjectionRecovery

        recovery = ProjectionRecovery(self.world, self.projections)
        recovered = recovery.recover()
        for name, count in recovered.items():
            if count > 0:
                print(f"[recovery] {name}: {count} receipts replayed", file=sys.stderr)

    def on(self, event_type: EventType, handler):
        self._handlers[event_type].append(handler)

    def emit(self, event: Event) -> Event:
        for handler in self._handlers.get(event.type, []):
            handler(event)
            if event.cancelled:
                break
        return event

    def run_v2(self, task: str, task_id: str | None = None) -> str:
        """Engineering task entry — delegates entirely to EngineeringOrchestrator."""
        # Fresh world session scope per orchestrated task (no cross-task session share)
        orch = EngineeringOrchestrator(
            project_root=self.workspace.project_root,
            world=self.world,
            projections=self.projections,
            planner=self._planner,
            checkpoint_store=self._task_memory,
        )
        return orch.run(task, task_id=task_id)

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

    def run(self, user_input: str) -> str:
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
        response = self.adapter.send(self.conversation.get_messages(), TOOL_DECLARATIONS)

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
                self.conversation.get_messages(), TOOL_DECLARATIONS
            )

        if self.phase != AgentPhase.DONE:
            self.phase = AgentPhase.DONE
        self.emit(Event(EventType.ASSISTANT_REPLY, {"content": response.content or ""}))
        if response.content:
            self.conversation.append(Message(role="assistant", content=response.content))
        return response.content or ""
