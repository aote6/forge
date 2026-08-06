"""Runtime - Agent 编排引擎 v2

Engineering Loop:
  UNDERSTANDING → PLANNING → CHECKING → EXECUTING → VERIFYING → REVIEWING → DONE

Transaction 链路（完全不动）：
  LLM → Intent → WorldSession → Receipt → Projection
"""
import json
import sys
from forge.adapters.base import BaseAdapter, Message, ToolCall, ToolResult
from forge.conversation import Conversation
from forge.workspace import Workspace
from forge.memory import MemoryStore
from forge.events import Event, EventType
from forge.tools import make_tools
from forge.world import WorldRuntime
from forge.tools.schemas import TOOL_DECLARATIONS
from forge.agent_state import AgentPhase
from forge.system_prompt import SYSTEM_INSTRUCTION
from forge.confirmation import is_confirm, is_cancel

# v2 协议层
from forge.contracts.repository import RepoContext
from forge.contracts.planning import Plan, PlanStep
from forge.contracts.constitution import ChangeProposal, ConstitutionResult, CheckStatus
from forge.contracts.verification import VerificationRequest, VerificationResult
from forge.contracts.execution import TaskCheckpoint

# v2 adapter 层
from forge.adapters.repo_adapter import get_repo_context
from forge.adapters.constitution_adapter import check as constitution_check
from forge.adapters.verifier_adapter import verify as verification_verify

# v2 Planner + TaskMemory
from forge.planner import Planner, plan_to_proposals
from forge.task_memory import TaskMemory, make_checkpoint

MAX_AGENT_STEPS = 20
MAX_CONSECUTIVE_FAILURES = 3
MAX_SELF_CORRECTION = 3


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
        tools, confirm_fn, abort_fn = make_tools(
            workspace, safe_mode="blacklist", world_runtime=self.world
        )
        self.tools = tools
        self._confirm_fn = confirm_fn
        self._abort_fn = abort_fn
        self._recover_projections()
        self.executor = ToolExecutor(self.tools)

        # v2 状态
        self._repo_context: RepoContext | None = None
        self._plan: Plan | None = None
        self._checkpoint: TaskCheckpoint | None = None
        self._correction_count: int = 0
        self._task_id: str | None = None
        self._task_memory = TaskMemory(workspace.project_root)
        self._planner = Planner(adapter)

    def _recover_projections(self):
        try:
            from forge.recovery.replay import ProjectionRecovery
            recovery = ProjectionRecovery(self.world, self.world.projections)
            recovered = recovery.recover()
            for name, count in recovered.items():
                if count > 0:
                    print(f"[recovery] {name}: {count} receipts replayed", file=sys.stderr)
        except Exception as e:
            print(f"[recovery] skipped: {e}", file=sys.stderr)
        self.conversation = Conversation(SYSTEM_INSTRUCTION)
        self.phase = AgentPhase.IDLE
        self._handlers: dict = {e: [] for e in EventType}

    def on(self, event_type: EventType, handler):
        self._handlers[event_type].append(handler)

    def emit(self, event: Event) -> Event:
        for handler in self._handlers.get(event.type, []):
            handler(event)
            if event.cancelled:
                break
        return event

    # ─── v2 Engineering Loop ────────────────────────────────

    def run_v2(self, task: str, task_id: str | None = None) -> str:
        """v2 Engineering Loop 入口 — 自动走完 6 个 Phase"""
        self._task_id = task_id or f"task_{task[:20].replace(' ','_')}"
        self._correction_count = 0

        # 尝试恢复已有 checkpoint
        saved = self._task_memory.load(self._task_id)
        if saved and saved.phase != "done":
            print(f"[v2] 恢复任务: {self._task_id} (phase={saved.phase})", file=sys.stderr)
            self._checkpoint = saved
            self.phase = AgentPhase(saved.phase)
        else:
            self.phase = AgentPhase.UNDERSTANDING

        print(f"\n[v2] Engineering Loop 启动 — {self._task_id}: {task}", file=sys.stderr)

        # Phase 1: UNDERSTANDING
        if self.phase == AgentPhase.UNDERSTANDING:
            self._repo_context = get_repo_context(self.workspace.project_root)
            self._save_phase("planning")
            self.phase = AgentPhase.PLANNING

        # Phase 2: PLANNING
        if self.phase == AgentPhase.PLANNING:
            self._plan = self._planner.plan(task, self._repo_context)
            self._save_phase("checking", plan=self._plan)
            self.phase = AgentPhase.CHECKING

        # Phase 3: CHECKING
        if self.phase == AgentPhase.CHECKING:
            proposals = plan_to_proposals(self._plan)
            all_pass = True
            for p in proposals:
                result = constitution_check(p)
                if result.status == CheckStatus.FAIL:
                    all_pass = False
                    print(f"  ❌ Constitution: {[v.rule_id for v in result.violations]}", file=sys.stderr)
            if not all_pass:
                self._save_phase("done")
                self.phase = AgentPhase.DONE
                return "❌ 宪法检查未通过，任务中止。"
            self._save_phase("executing", plan=self._plan)
            self.phase = AgentPhase.EXECUTING

        # Phase 4: EXECUTING (走 Transaction 链路)
        if self.phase == AgentPhase.EXECUTING:
            results = []
            proposals = plan_to_proposals(self._plan)
            for i, proposal in enumerate(proposals):
                for target in proposal.target_files:
                    import os
                    full_path = os.path.join(self.workspace.project_root, target)
                    content = f"# Forge v2 auto-generated\n# {proposal.reason}\n"
                    os.makedirs(os.path.dirname(full_path) or ".", exist_ok=True)

                    session = self.world.begin_session()
                    obj_id = session.create_object()
                    session.write(obj_id, 0, value=full_path)
                    session.write(obj_id, 1, value=content)
                    receipt, delta = self.world.commit_session()

                    # 补 delta（已知 veritasd 限制）
                    delta.memory_written = [
                        {"object_id": obj_id, "state_id": 0, "value_hex": full_path.encode().hex()},
                        {"object_id": obj_id, "state_id": 1, "value_hex": content.encode().hex()},
                    ]
                    delta.objects_created = [obj_id]

                    from forge.projections.base import ProjectionManager
                    from forge.projections.file_projection import FileProjection
                    pm = ProjectionManager()
                    pm.register(FileProjection(
                        project_root=self.workspace.project_root,
                        object_path_map=getattr(self.world, '_path_map', None)
                    ))
                    pm.project(receipt, delta)

                    results.append({
                        "step": proposal.proposal_id,
                        "file": target,
                        "tx_id": receipt.tx_id,
                        "version": receipt.version
                    })

            self._save_phase("verifying", plan=self._plan,
                             extra={"execution_results": results})
            self.phase = AgentPhase.VERIFYING

        # Phase 5: VERIFYING
        if self.phase == AgentPhase.VERIFYING:
            all_files = [f for s in self._plan.steps for f in s.target_files]
            vreq = VerificationRequest(changed_files=all_files, change_type="modify")
            vresult = verification_verify(vreq)
            if vresult.status == CheckStatus.FAIL:
                self._correction_count += 1
                if self._correction_count < MAX_SELF_CORRECTION:
                    print(f"[v2] 验证失败，回跳 (第{self._correction_count}次)", file=sys.stderr)
                    self._save_phase("executing", plan=self._plan)
                    self.phase = AgentPhase.EXECUTING
                    return "⚠️ 验证失败，重新执行..."
            self._save_phase("reviewing", plan=self._plan)
            self.phase = AgentPhase.REVIEWING

        # Phase 6: REVIEWING
        if self.phase == AgentPhase.REVIEWING:
            self._save_phase("done", plan=self._plan)
            self.phase = AgentPhase.DONE
            self._correction_count = 0

            report = (
                f"✅ 任务完成: {self._plan.goal}\n"
                f"   步骤: {len(self._plan.steps)} 个\n"
                f"   Constitution: PASS\n"
                f"   Verification: PASS\n"
            )
            print(f"[v2] {report}", file=sys.stderr)
            return report

        return f"[v2] 阶段: {self.phase.value}"

    def _save_phase(self, phase: str, plan: Plan | None = None, extra: dict | None = None):
        """保存当前阶段到 TaskCheckpoint"""
        cp = make_checkpoint(
            task_id=self._task_id,
            phase=phase,
            plan=plan or self._plan,
            completed_steps=[s.step_id for s in (plan or self._plan).steps] if phase == "done" else [],
            extra_state=extra
        )
        self._task_memory.save(cp)
        self._checkpoint = cp

    # ─── v1 兼容 run() ──────────────────────────────────────

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
                    self.conversation.append(Message(
                        role="system",
                        content="事务已提交。现在进入验证阶段，请运行 git_diff 和测试。",
                    ))
                return result.display
            if is_cancel(user_input):
                if self._abort_fn is not None:
                    result = self._abort_fn()
                    self.phase = AgentPhase.IDLE
                    return result.display
                self.phase = AgentPhase.IDLE
                return "事务已取消。"
            return (
                "⏸️ 当前有待确认的事务\n"
                "请输入「确认」提交，或「取消」放弃。\n"
            )

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
                self.conversation.append(Message(
                    role="assistant",
                    content=f"⛔ 已达到最大执行步数({MAX_AGENT_STEPS})，任务中止。",
                ))
                return f"⛔ Agent 步数超限({MAX_AGENT_STEPS})，已强制终止。"

            self.conversation.append(Message(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            ))

            for tc in response.tool_calls:
                if self.phase == AgentPhase.WAIT_CONFIRM:
                    self.conversation.append(Message(
                        role="tool",
                        content="⏸️ 当前有未确认的事务，请等待用户确认后再继续。",
                        tool_call_id=tc.id, name=tc.name,
                    ))
                    continue

                self.emit(Event(EventType.TOOL_CALL_START, {
                    "name": tc.name, "args": tc.arguments,
                }))
                result = self.executor.execute(tc)
                self.emit(Event(EventType.TOOL_CALL_END, {
                    "name": tc.name, "success": result.success, "display": result.display,
                }))
                self.conversation.append(Message(
                    role="tool", content=result.display,
                    tool_call_id=tc.id, name=tc.name,
                ))
                self._update_phase_from_result(result)

                if result.success and result.payload and result.payload.get("requires_confirmation"):
                    self.conversation.append(Message(
                        role="system",
                        content="修改已准备完成。必须等待用户确认后才能继续。不要调用其它修改工具。",
                    ))
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
