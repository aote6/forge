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

    def _recover_projections(self):
        """启动时从 Veritas WAL 恢复所有 Projection。"""
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

    def _update_phase_from_result(self, result: ToolResult):
        """根据工具 payload 推进阶段，不依赖工具名列表。"""
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

    # ─── v2 Engineering Loop ────────────────────────────────

    def _step_understand(self, task: str) -> RepoContext:
        """Phase 1: 仓库理解 → RepoContext"""
        print("[v2] UNDERSTANDING — 正在获取仓库上下文...", file=sys.stderr)
        ctx = get_repo_context(self.workspace.project_root)
        self._repo_context = ctx
        self.phase = AgentPhase.PLANNING
        print(f"[v2] UNDERSTANDING 完成 — {len(ctx.file_tree)} 个文件", file=sys.stderr)
        return ctx

    def _step_plan(self, task: str) -> Plan:
        """Phase 2: 规划 → Plan"""
        print("[v2] PLANNING — 正在生成执行计划...", file=sys.stderr)
        self.conversation.append(Message(
            role="system",
            content=(
                f"你是一个代码规划器。根据以下仓库上下文和任务，"
                f"生成一个有序的修改计划。\n\n"
                f"仓库文件列表:\n{chr(10).join(self._repo_context.file_tree[:50])}\n\n"
                f"任务: {task}\n\n"
                f"输出格式: 每行一个步骤，格式为 '文件名: 操作类型: 描述'"
            )
        ))
        response = self.adapter.send(
            self.conversation.get_messages(), TOOL_DECLARATIONS
        )
        # TODO: 解析 LLM 输出为 Plan 结构
        self._plan = Plan(
            plan_id=f"plan_{self._repo_context.commit_hash[:8]}",
            goal=task,
            steps=[],
            assumptions=[]
        )
        self.phase = AgentPhase.CHECKING
        print(f"[v2] PLANNING 完成", file=sys.stderr)
        return self._plan

    def _step_check(self, plan: Plan) -> ConstitutionResult:
        """Phase 3: 宪法检查 → ConstitutionResult"""
        print("[v2] CHECKING — 正在运行宪法检查...", file=sys.stderr)
        all_files = []
        for step in plan.steps:
            all_files.extend(step.target_files)
        proposal = ChangeProposal(
            proposal_id=plan.plan_id,
            plan_id=plan.plan_id,
            target_files=list(set(all_files)),
            operations=[],
            reason=plan.goal,
            expected_effects=[]
        )
        result = constitution_check(proposal)
        if result.status == CheckStatus.FAIL:
            print(f"[v2] CHECKING 失败 — {len(result.violations)} 条违规", file=sys.stderr)
            for v in result.violations:
                print(f"  - {v.rule_id}: {v.message}", file=sys.stderr)
            self.phase = AgentPhase.DONE
        else:
            print("[v2] CHECKING 通过", file=sys.stderr)
            self.phase = AgentPhase.EXECUTING
        return result

    def _step_execute(self, plan: Plan) -> str:
        """Phase 4: 执行 — 走现有 Transaction 链路"""
        print("[v2] EXECUTING — 进入 Transaction 链路...", file=sys.stderr)
        # 退回到现有 Runtime.run() 的 Transaction 链路
        # 把 plan 的步骤注入 conversation 作为 LLM 上下文
        steps_text = "\n".join(
            f"{i+1}. {s.target_files} — {s.operation_type} — {s.description}"
            for i, s in enumerate(plan.steps)
        )
        self.conversation.append(Message(
            role="system",
            content=f"执行以下计划:\n{steps_text}\n\n请按顺序修改文件。"
        ))
        self.phase = AgentPhase.DISCOVERY  # 复用现有 Transaction 链路
        return steps_text

    def _step_verify(self) -> VerificationResult:
        """Phase 5: 验证 → VerificationResult"""
        print("[v2] VERIFYING — 正在运行验证...", file=sys.stderr)
        all_files = []
        if self._plan:
            for step in self._plan.steps:
                all_files.extend(step.target_files)
        request = VerificationRequest(
            changed_files=list(set(all_files)),
            change_type="modify"
        )
        result = verification_verify(request)
        if result.status == CheckStatus.FAIL:
            print(f"[v2] VERIFYING 失败 — {len(result.failures)} 项失败", file=sys.stderr)
            self._correction_count += 1
            if self._correction_count < MAX_SELF_CORRECTION:
                print(f"[v2] 回跳到 EXECUTING (第{self._correction_count}次纠错)", file=sys.stderr)
                self.phase = AgentPhase.EXECUTING
            else:
                print(f"[v2] 已达最大纠错次数({MAX_SELF_CORRECTION})，任务挂起", file=sys.stderr)
                self.phase = AgentPhase.DONE
        else:
            print("[v2] VERIFYING 通过", file=sys.stderr)
            self.phase = AgentPhase.REVIEWING
        return result

    def _step_review(self) -> str:
        """Phase 6: 审查 → 完成"""
        print("[v2] REVIEWING — 生成完成报告...", file=sys.stderr)
        self.phase = AgentPhase.DONE
        self._correction_count = 0
        report = "✅ 任务完成。所有步骤已执行，宪法检查通过，验证通过。"
        print(f"[v2] {report}", file=sys.stderr)
        return report

    # ─── 公开入口 ──────────────────────────────────────────

    def run_v2(self, task: str) -> str:
        """v2 Engineering Loop 入口"""
        print(f"\n[v2] Engineering Loop 启动 — 任务: {task}", file=sys.stderr)

        # Phase 1: UNDERSTANDING
        self.phase = AgentPhase.UNDERSTANDING
        repo_ctx = self._step_understand(task)

        # Phase 2: PLANNING
        plan = self._step_plan(task)

        # Phase 3: CHECKING
        check_result = self._step_check(plan)
        if check_result.status == CheckStatus.FAIL:
            return f"❌ 宪法检查未通过:\n" + "\n".join(
                f"  - {v.rule_id}: {v.message}" for v in check_result.violations
            )

        # Phase 4: EXECUTING (走现有 Transaction 链路)
        self._step_execute(plan)

        # Phase 5-6 由 _update_phase_from_result 和 self correction 驱动
        # 此处返回执行上下文，后续交互由 run() 继续处理
        return f"📋 计划已生成，开始执行 {len(plan.steps)} 个步骤。请继续。"

    def run(self, user_input: str) -> str:
        # === WAIT_CONFIRM: 用户确认/取消 ===
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
                "（其他指令暂不支持，请先处理当前事务）"
            )

        # === v2 Phase 衔接：VERIFYING 完成后走 REVIEWING ===
        if self.phase == AgentPhase.VERIFYING and self._plan is not None:
            verify_result = self._step_verify()
            if self.phase == AgentPhase.REVIEWING:
                report = self._step_review()
                self.conversation.append(Message(role="assistant", content=report))
                return report
            if self.phase == AgentPhase.EXECUTING:
                # Self correction: 回跳执行
                return "⚠️ 验证失败，正在重新执行..."

        # === 正常任务流程 (v1 兼容) ===
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
                        tool_call_id=tc.id,
                        name=tc.name,
                    ))
                    continue

                self.emit(Event(EventType.TOOL_CALL_START, {
                    "name": tc.name, "args": tc.arguments,
                }))
                result = self.executor.execute(tc)
                self.emit(Event(EventType.TOOL_CALL_END, {
                    "name": tc.name,
                    "success": result.success,
                    "display": result.display,
                }))
                self.conversation.append(Message(
                    role="tool",
                    content=result.display,
                    tool_call_id=tc.id,
                    name=tc.name,
                ))

                self._update_phase_from_result(result)

                if (
                    result.success
                    and result.payload
                    and result.payload.get("requires_confirmation")
                ):
                    self.conversation.append(Message(
                        role="system",
                        content=(
                            "修改已准备完成。必须等待用户确认后才能继续。"
                            "不要调用其它修改工具。"
                        ),
                    ))
                    return result.display

            response = self.adapter.send(
                self.conversation.get_messages(), TOOL_DECLARATIONS
            )

        # v1 完成逻辑
        if self.phase != AgentPhase.DONE:
            self.phase = AgentPhase.DONE
        self.emit(Event(EventType.ASSISTANT_REPLY, {"content": response.content or ""}))
        if response.content:
            self.conversation.append(Message(role="assistant", content=response.content))
        return response.content or ""
