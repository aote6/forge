"""Runtime - 只负责 receive → dispatch → reply"""
from forge.adapters.base import BaseAdapter, Message, ToolResult
from forge.conversation import Conversation
from forge.workspace import Workspace
from forge.memory import MemoryStore
from forge.events import Event, EventType
from forge.tools import make_tools
from forge.tools.schemas import TOOL_DECLARATIONS


SYSTEM_INSTRUCTION = """
你是 Forge 运行时中的 AI 协作者。
修改文件分两步：prepare_write → 用户确认 → commit_write。
operations 使用 anchor（函数名/类名）定位，不用行号。
不确定就说不确定。
"""


class ToolExecutor:
    def __init__(self, tools: dict):
        self.tools = tools
    
    def execute(self, tool_call) -> ToolResult:
        fn = self.tools.get(tool_call.name)
        if not fn:
            return ToolResult.fail(display=f"未知工具: {tool_call.name}")
        return fn(**tool_call.arguments)


class Runtime:
    def __init__(self, adapter: BaseAdapter, workspace: Workspace, memory: MemoryStore):
        self.adapter = adapter
        self.workspace = workspace
        self.memory = memory
        self.tools = make_tools(workspace)
        self.executor = ToolExecutor(self.tools)
        self.conversation = Conversation(SYSTEM_INSTRUCTION)
        self._handlers: dict = {e: [] for e in EventType}
    
    def on(self, event_type: EventType, handler):
        self._handlers[event_type].append(handler)
    
    def emit(self, event: Event) -> Event:
        for handler in self._handlers.get(event.type, []):
            handler(event)
            if event.cancelled:
                break
        return event
    
    def run(self, user_input: str) -> str:
        event = self.emit(Event(EventType.USER_MESSAGE, {"content": user_input}))
        if event.cancelled:
            return "⏸️ 已拦截。"
        
        self.conversation.append(Message(role="user", content=user_input))
        
        response = self.adapter.send(self.conversation.get_messages(), TOOL_DECLARATIONS)
        
        while response.tool_calls:
            # 先记录 assistant 消息（含 tool_calls）
            self.conversation.append(Message(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls
            ))
            
            # 执行每个 tool call，立即追加 tool 结果
            for tc in response.tool_calls:
                self.emit(Event(EventType.TOOL_CALL_START, {"name": tc.name, "args": tc.arguments}))
                result = self.executor.execute(tc)
                self.emit(Event(EventType.TOOL_CALL_END, {
                    "name": tc.name, "success": result.success, "display": result.display
                }))
                self.conversation.append(Message(
                    role="tool", content=result.display,
                    tool_call_id=tc.id, name=tc.name
                ))
            
            response = self.adapter.send(self.conversation.get_messages(), TOOL_DECLARATIONS)
        
        self.emit(Event(EventType.ASSISTANT_REPLY, {"content": response.content or ""}))
        if response.content:
            self.conversation.append(Message(role="assistant", content=response.content))
        
        return response.content or ""
