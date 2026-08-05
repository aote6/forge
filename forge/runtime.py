"""Runtime - Agent 编排引擎"""
import json
from forge.adapters.base import BaseAdapter, Message, ToolCall, ToolResult
from forge.conversation import Conversation
from forge.workspace import Workspace
from forge.memory import MemoryStore
from forge.events import Event, EventType
from forge.tools import make_tools
from forge.tools.schemas import TOOL_DECLARATIONS
from forge.agent_state import AgentPhase
from forge.system_prompt import SYSTEM_INSTRUCTION
from forge.confirmation import extract_confirmation

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
        self.tools = make_tools(workspace, safe_mode="blacklist")
        self.executor = ToolExecutor(self.tools)
        self.conversation = Conversation(SYSTEM_INSTRUCTION)
        self.phase = AgentPhase.IDLE
        self.pending_transaction = None
        self._handlers: dict = {e: [] for e in EventType}

    def on(self, event_type: EventType, handler):
        self._handlers[event_type].append(handler)

    def emit(self, event: Event) -> Event:
        for handler in self._handlers.get(event.type, []):
            handler(event)
            if event.cancelled:
                break
        return event

    def _update_phase_after_tool(self, tool_name: str, result: ToolResult):
        """根据工具执行结果自动推进任务阶段"""
        if not result.success:
            return

        if tool_name in ("list_files", "read_file", "search_code"):
            if self.phase in (AgentPhase.IDLE, AgentPhase.DISCOVERY):
                self.phase = AgentPhase.DISCOVERY
        elif tool_name == "prepare_write":
            self.phase = AgentPhase.WAIT_CONFIRM
            if result.payload:
                self.pending_transaction = result.payload.get("transaction_id")
        elif tool_name == "commit_write":
            self.phase = AgentPhase.VERIFYING
            self.pending_transaction = None
        elif tool_name in ("git_diff", "run_command"):
            if self.phase == AgentPhase.VERIFYING:
                self.phase = AgentPhase.REPORT

    def run(self, user_input: str) -> str:
        # === 处理 WAIT_CONFIRM 状态：用户确认/取消事务 ===
        if self.phase == AgentPhase.WAIT_CONFIRM:
            txid = extract_confirmation(user_input)
            if txid:
                result = self.executor.execute(
                    ToolCall(
                        id="user-confirm",
                        name="commit_write",
                        arguments={"transaction_id": txid}
                    )
                )
                if result.success:
                    self.phase = AgentPhase.VERIFYING
                    self.conversation.append(Message(
                        role="system",
                        content="事务已提交。现在进入验证阶段，请运行 git_diff 和测试。"
                    ))
                    return result.display
                else:
                    return result.display

            # 检查是否取消
            if user_input.strip().startswith("取消"):
                if self.pending_transaction:
                    self.executor.execute(
                        ToolCall(
                            id="user-cancel",
                            name="cancel_write",
                            arguments={"transaction_id": self.pending_transaction}
                        )
                    )
                self.phase = AgentPhase.IDLE
                self.pending_transaction = None
                return "事务已取消。"

            # 不是确认也不是取消，提示用户
            return (
                f"⏸️ 当前有待确认的事务: {self.pending_transaction}\n"
                f"请输入 '确认 {self.pending_transaction}' 提交，或 '取消' 放弃。\n"
                f"（其他指令暂不支持，请先处理当前事务）"
            )

        # === 正常任务流程 ===
        self.executor.reset()

        # 只在非 WAIT_CONFIRM 状态下重置阶段
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
                    content=f"⛔ 已达到最大执行步数({MAX_AGENT_STEPS})，任务中止。"
                ))
                return f"⛔ Agent 步数超限({MAX_AGENT_STEPS})，已强制终止。"

            self.conversation.append(Message(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls
            ))

            for tc in response.tool_calls:
                # WAIT_CONFIRM 状态下拒绝修改类工具
                if self.phase == AgentPhase.WAIT_CONFIRM:
                    if tc.name in ("prepare_write", "commit_write"):
                        self.conversation.append(Message(
                            role="tool",
                            content="⏸️ 当前有未确认的事务，请等待用户确认后再继续。",
                            tool_call_id=tc.id,
                            name=tc.name
                        ))
                        continue

                self.emit(Event(EventType.TOOL_CALL_START, {
                    "name": tc.name, "args": tc.arguments
                }))
                result = self.executor.execute(tc)
                self.emit(Event(EventType.TOOL_CALL_END, {
                    "name": tc.name,
                    "success": result.success,
                    "display": result.display
                }))
                self.conversation.append(Message(
                    role="tool",
                    content=result.display,
                    tool_call_id=tc.id,
                    name=tc.name
                ))

                # 更新阶段
                self._update_phase_after_tool(tc.name, result)

                # prepare_write 成功后立即暂停，等待用户确认
                if (result.success
                        and result.payload
                        and result.payload.get("requires_confirmation")):
                    self.conversation.append(Message(
                        role="system",
                        content=(
                            "文件修改已准备完成。必须等待用户确认后才能继续。"
                            "不要调用其它修改工具。"
                        )
                    ))
                    return result.display

            response = self.adapter.send(
                self.conversation.get_messages(), TOOL_DECLARATIONS
            )

        self.phase = AgentPhase.DONE
        self.emit(Event(EventType.ASSISTANT_REPLY, {"content": response.content or ""}))
        if response.content:
            self.conversation.append(Message(role="assistant", content=response.content))
        return response.content or ""
