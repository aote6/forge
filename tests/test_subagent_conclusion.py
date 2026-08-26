"""P1-7: 子代理结论强制结构化契约测试。

验证 spawn_subagent / run_subagent 只把四段式最终结论摘要交还主循环，
绝不把中间 tool-call 轨迹注入主上下文；同时保持返回值为 str 的既有契约。
"""
from __future__ import annotations

from unittest.mock import MagicMock

from forge.adapters.base import ToolCall, ToolResult
from forge.agent_abi import AgentResult, AgentTask
from forge.subagent import (
    structure_conclusion,
    run_subagent,
    filter_schemas_for_subagent,
)
from forge.tools.schemas import READ_ONLY_TOOL_DECLARATIONS, MUTATION_TOOL_DECLARATIONS


def _sections(text: str) -> dict[str, str]:
    """解析四段式文本为 {SECTION: body}，便于逐段断言。"""
    out: dict[str, str] = {}
    current = None
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped in ("CONCLUSION:", "EVIDENCE:", "UNCERTAIN:", "NEXT:"):
            current = stripped.rstrip(":")
            out[current] = ""
            continue
        if current is not None:
            out[current] = (out[current] + "\n" + line).strip("\n")
    return out


# --- structure_conclusion 纯函数契约 ---

def test_structure_conclusion_wraps_free_text():
    out = structure_conclusion("bug 在 a.py 的校验逻辑里")
    sec = _sections(out)
    assert sec["CONCLUSION"] == "bug 在 a.py 的校验逻辑里"
    assert sec["EVIDENCE"] == "(无)"
    assert sec["UNCERTAIN"] == "(无)"
    assert sec["NEXT"] == "(无)"


def test_structure_conclusion_keeps_all_sections():
    raw = (
        "CONCLUSION:\n"
        "bug 在 str_replace 的 old_string 匹配处\n"
        "EVIDENCE:\n"
        "- forge/tools/intent_tools.py:834 缺少去空白\n"
        "UNCERTAIN:\n"
        "未验证 replace_all=true 分支\n"
        "NEXT:\n"
        "run_test_structured(target='tests/') 验证\n"
    )
    out = structure_conclusion(raw)
    sec = _sections(out)
    assert "old_string 匹配处" in sec["CONCLUSION"]
    assert "forge/tools/intent_tools.py:834" in sec["EVIDENCE"]
    assert "replace_all" in sec["UNCERTAIN"]
    assert "run_test_structured" in sec["NEXT"]


def test_structure_conclusion_evidence_preserves_path_line():
    raw = "CONCLUSION:\n定位到\nEVIDENCE:\n- forge/subagent.py:12\n"
    out = structure_conclusion(raw)
    sec = _sections(out)
    assert "- forge/subagent.py:12" in sec["EVIDENCE"]


def test_structure_conclusion_fills_missing_sections():
    raw = "CONCLUSION:\n找到问题\nEVIDENCE:\n- a.py:3\n"
    out = structure_conclusion(raw)
    sec = _sections(out)
    assert sec["CONCLUSION"] == "找到问题"
    assert sec["EVIDENCE"] == "- a.py:3"
    assert sec["UNCERTAIN"] == "(无)"
    assert sec["NEXT"] == "(无)"


def test_structure_conclusion_empty():
    out = structure_conclusion("")
    assert out.startswith("CONCLUSION:")
    assert "empty conclusion" in out


def test_structure_conclusion_never_invents_evidence():
    out = structure_conclusion("随便一句结论")
    sec = _sections(out)
    assert sec["EVIDENCE"] == "(无)"
    assert sec["UNCERTAIN"] == "(无)"


# --- run_subagent 端到端契约（不依赖 veritasd）---

_SUB_SCHEMAS = filter_schemas_for_subagent(
    list(READ_ONLY_TOOL_DECLARATIONS) + list(MUTATION_TOOL_DECLARATIONS)
)


def test_run_subagent_returns_structured_conclusion():
    class FakeAdapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            if self.n == 1:
                return MagicMock(
                    content="",
                    tool_calls=[ToolCall(id="1", name="search_code", arguments={"pattern": "x"})],
                )
            return MagicMock(
                content="CONCLUSION:\nbug 在 a.py\nEVIDENCE:\n- a.py:3\nUNCERTAIN:\n无\nNEXT:\n无\n",
                tool_calls=None,
            )

    tools = {"search_code": lambda pattern, path=".": ToolResult.ok(display="a.py:3:x")}
    out = run_subagent(
        FakeAdapter(),
        tools,
        _SUB_SCHEMAS,
        AgentTask(goal="find bug", max_steps=5),
    )
    assert isinstance(out, AgentResult)
    assert "a.py" in out.conclusion
    assert len(out.evidence) == 0  # no tool_call_id in EVIDENCE line


def test_run_subagent_intermediate_tool_trace_not_in_main_context():
    """子代理内部 tool 结果绝不能泄漏进主循环收到的结论。"""
    class FakeAdapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            if self.n == 1:
                return MagicMock(
                    content="",
                    tool_calls=[ToolCall(id="1", name="search_code", arguments={"pattern": "x"})],
                )
            return MagicMock(
                content="CONCLUSION:\n最终结论\nEVIDENCE:\n- a.py:3\nUNCERTAIN:\n无\nNEXT:\n无\n",
                tool_calls=None,
            )

    tools = {
        "search_code": lambda pattern, path=".": ToolResult.ok(display="INTERMEDIATE_SECRET_TRACE a.py:3"),
    }
    out = run_subagent(
        FakeAdapter(),
        tools,
        _SUB_SCHEMAS,
        AgentTask(goal="find bug", max_steps=5),
    )
    assert isinstance(out, AgentResult)
    assert "INTERMEDIATE_SECRET_TRACE" not in out.conclusion
    assert "CONCLUSION:" not in out.conclusion


def test_run_subagent_returns_str_for_compat():
    """既有调用方依赖返回 str（而非 dict/ToolResult），结构不破坏该契约。"""
    class FakeAdapter:
        def send(self, messages, schemas):
            return MagicMock(content="自由结论", tool_calls=None)

    tools: dict = {}
    out = run_subagent(
        FakeAdapter(),
        tools,
        _SUB_SCHEMAS,
        AgentTask(goal="task", max_steps=3),
    )
    assert isinstance(out, AgentResult)
    assert out.status in {"done", "blocked", "need_decision"}
