"""测试 P0/P1 新增的只读/查询工具。"""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forge.adapters.base import ToolResult
from forge.workspace import Workspace
from forge.tools.local_tools import make_local_tools


@pytest.fixture
def tools(tmp_path):
    workspace = Workspace(str(tmp_path))
    return make_local_tools(workspace)


def test_get_repo_map(tools, tmp_path):
    # 创建一个测试 Python 文件
    src = tmp_path / "src"
    src.mkdir()
    p = src / "demo.py"
    p.write_text("class Foo:\n    def bar(self, x):\n        return x\n\ndef helper(y):\n    return y\n")

    result = tools["get_repo_map"]()
    assert result.success
    assert "class Foo" in result.display
    assert "def bar" in result.display
    assert "def helper" in result.display


def test_read_files(tools, tmp_path):
    f1 = tmp_path / "a.txt"
    f1.write_text("line1\nline2\nline3\n")
    f2 = tmp_path / "b.txt"
    f2.write_text("hello\nworld\n")

    result = tools["read_files"](requests=[
        {"path": "a.txt", "start_line": 1, "end_line": 2},
        {"path": "b.txt"},
    ])
    assert result.success
    assert "line1" in result.display
    assert "line2" in result.display
    assert "line3" not in result.display
    assert "hello" in result.display
    assert "world" in result.display


def test_read_files_missing_path(tools):
    result = tools["read_files"](requests=[{"path": "nonexistent.txt"}])
    assert result.success  # 工具本身不 fail，只是标记文件不存在
    assert "Error" in result.display or "not found" in result.display.lower()


def test_read_files_empty_requests(tools):
    result = tools["read_files"](requests=[])
    assert not result.success
    assert "非空" in result.display


def test_run_test_structured(tools):
    result = tools["run_test_structured"](target="tests/")
    # 应该返回结构化结果（可能通过也可能失败，但格式正确）
    assert isinstance(result, ToolResult)
    # 有返回内容
    assert len(result.display) > 0


def test_run_diagnostics_clean(tools, tmp_path):
    # 空项目应该 clean
    result = tools["run_diagnostics"](directory=".")
    assert result.success
    parsed = json.loads(result.display)
    assert parsed["status"] == "clean"
    assert parsed["error_count"] == 0


def test_run_diagnostics_syntax_error(tools, tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(\n")
    result = tools["run_diagnostics"](directory=".")
    assert not result.success
    parsed = json.loads(result.display)
    assert parsed["status"] == "issues_found"
    assert parsed["error_count"] >= 1


def test_get_context_budget(tools, tmp_path):
    f1 = tmp_path / "data.txt"
    f1.write_text("a" * 400)  # 约 100 tokens

    result = tools["get_context_budget"](tracked_files=["data.txt"])
    assert result.success
    parsed = json.loads(result.display)
    assert parsed["total_estimated_tokens"] == 100
    assert parsed["status"] == "ok"


def test_get_context_budget_no_files(tools):
    result = tools["get_context_budget"]()
    assert result.success
    parsed = json.loads(result.display)
    assert parsed["total_estimated_tokens"] == 0


def test_inspect_last_intent_none(tools):
    result = tools["inspect_last_intent"](history_file=".forge/nonexistent.json")
    assert result.success
    parsed = json.loads(result.display)
    assert parsed["status"] == "none"


def test_inspect_last_intent_file(tools, tmp_path):
    hist = tmp_path / ".forge" / "last_intent.json"
    hist.parent.mkdir(parents=True, exist_ok=True)
    hist.write_text(json.dumps({"tx_id": "tx_123", "status": "committed"}))

    result = tools["inspect_last_intent"](history_file=".forge/last_intent.json")
    assert result.success
    parsed = json.loads(result.display)
    assert parsed["tx_id"] == "tx_123"


def test_world_tools_present(tools):
    """确认所有 World 查询工具都注册了。"""
    for name in ["world_info", "list_world_objects", "get_world_object", "list_world_links"]:
        assert name in tools, f"缺少工具: {name}"


# ========== 第二批：5 个增强工具测试 ==========

def test_git_status_enhanced(tools, tmp_path):
    """git status 在非 git 目录应返回 HEAD (detached)。"""
    result = tools["git_status_enhanced"]()
    assert isinstance(result, ToolResult)
    # 在非 git 目录，git branch 会失败但工具仍返回 ok（显示 detached）
    assert "Branch:" in result.display


def test_list_tests(tools, tmp_path):
    """能找到 test_*.py 文件。"""
    test_file = tmp_path / "test_dummy.py"
    test_file.write_text("def test_ok(): pass\n")
    result = tools["list_tests"]()
    assert result.success
    assert "test_dummy.py" in result.display
    assert "test_dummy.py" in result.payload["tests"]


def test_list_tests_nonexistent_dir(tools):
    """不存在的目录应 fail。"""
    result = tools["list_tests"](directory="non_existent")
    assert not result.success


def test_read_git_version_no_git(tools):
    """非 git 项目应 fail。"""
    result = tools["read_git_version"](path="nonexistent.py", revision="HEAD")
    assert not result.success


def test_search_history_no_log(tools):
    """无日志文件时返回 ok + empty。"""
    result = tools["search_history"](query="anything")
    assert result.success
    assert result.payload["matches"] == []


def test_search_history_with_log(tools, tmp_path):
    """有日志文件时能找到匹配。"""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir(exist_ok=True)
    log_file = forge_dir / "conversation_log.jsonl"
    log_file.write_text(
        json.dumps({"role": "user", "content": "我的项目内核叫 Veritas"}) + "\n" +
        json.dumps({"role": "assistant", "content": "好的收到"}) + "\n"
    )
    result = tools["search_history"](query="Veritas")
    assert result.success
    assert "Veritas" in result.display
    assert len(result.payload["matches"]) == 1


def test_search_history_no_match(tools, tmp_path):
    """无匹配时返回 ok + empty。"""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir(exist_ok=True)
    log_file = forge_dir / "conversation_log.jsonl"
    log_file.write_text(json.dumps({"role": "user", "content": "hello"}) + "\n")
    result = tools["search_history"](query="Nonexistent")
    assert result.success
    assert result.payload["matches"] == []


def test_summarize_file(tools, tmp_path):
    """第一次生成摘要，第二次命中缓存。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import os\n"
        "class Engine:\n"
        "    def run(self, val): pass\n\n"
        "def helper(x): return x\n"
    )
    result1 = tools["summarize_file"](path="sample.py")
    assert result1.success
    assert result1.payload["cached"] is False
    assert "Engine" in result1.payload["summary"]["classes"]
    assert "run(self, val)" in result1.payload["summary"]["functions"]
    assert "os" in result1.payload["summary"]["imports"]

    result2 = tools["summarize_file"](path="sample.py")
    assert result2.success
    assert result2.payload["cached"] is True


def test_summarize_file_missing(tools):
    """文件不存在应 fail。"""
    result = tools["summarize_file"](path="missing.py")
    assert not result.success




# ========== 第三批：4 个高级工具测试 ==========

def test_find_symbol_definition(tools, tmp_path):
    sample = tmp_path / "engine.py"
    sample.write_text(
        'class Engine:\n'
        '    """Core engine class"""\n'
        '    def run(self, x): pass\n'
    )
    result = tools["find_symbol_definition"](symbol_name="Engine")
    assert result.success
    assert "engine.py" in result.display


def test_find_symbol_definition_not_found(tools):
    result = tools["find_symbol_definition"](symbol_name="NonexistentSymbol")
    assert result.success
    assert result.payload["matches"] == []


def test_get_call_chain(tools, tmp_path):
    sample = tmp_path / "main.py"
    sample.write_text(
        'def target():\n'
        '    helper()\n'
        '\n'
        'def helper(): pass\n'
        '\n'
        'def caller():\n'
        '    target()\n'
    )
    result = tools["get_call_chain"](symbol_name="target")
    assert result.success
    assert "helper" in result.payload["callees"]


def test_get_diff_summary(tools, tmp_path):
    result = tools["get_diff_summary"]()
    assert isinstance(result, ToolResult)
    assert len(result.display) > 0


def test_extract_code_skeleton(tools, tmp_path):
    sample = tmp_path / "skeleton.py"
    sample.write_text(
        'import os\n'
        '\n'
        'class Foo:\n'
        '    def bar(self):\n'
        '        x = 1\n'
        '        return x\n'
        '\n'
        'def standalone(y):\n'
        '    return y * 2\n'
    )
    result = tools["extract_code_skeleton"](path="skeleton.py")
    assert result.success
    assert "class Foo" in result.display
    assert "def bar" in result.display
    assert "def standalone" in result.display
    assert "x = 1" not in result.display


def test_extract_code_skeleton_missing(tools):
    result = tools["extract_code_skeleton"](path="nonexistent.py")
    assert not result.success


# ========== 第四批：3 个精准工具测试 ==========

def test_read_file_with_lines(tools, tmp_path):
    f = tmp_path / "code.py"
    f.write_text('def foo():\n    return 1\n\ndef bar():\n    return 2\n')
    result = tools["read_file_with_lines"](path="code.py")
    assert result.success
    assert "1 | def foo():" in result.display
    assert "2 |     return 1" in result.display
    assert "4 | def bar():" in result.display
    assert result.payload["total_lines"] == 5


def test_read_file_with_lines_range(tools, tmp_path):
    f = tmp_path / "range.py"
    f.write_text('a\nb\nc\nd\ne\n')
    result = tools["read_file_with_lines"](path="range.py", start_line=2, end_line=4)
    assert result.success
    assert "2 | b" in result.display
    assert "3 | c" in result.display
    assert "4 | d" in result.display
    assert "1 | a" not in result.display
    assert "5 | e" not in result.display


def test_read_file_with_lines_missing(tools):
    result = tools["read_file_with_lines"](path="nonexistent.py")
    assert not result.success


def test_preview_line_mutation(tools, tmp_path):
    f = tmp_path / "mutate.py"
    f.write_text('def old():\n    return 1\n\ndef keep():\n    return 2\n')
    result = tools["preview_line_mutation"](
        path="mutate.py", start_line=1, end_line=2, new_text="def new():\n    return 99"
    )
    assert result.success
    assert "def new():" in result.display
    assert "def keep():" in result.display  # 后续上下文


def test_preview_line_mutation_invalid_range(tools, tmp_path):
    f = tmp_path / "bad_range.py"
    f.write_text('a\nb\nc\n')
    result = tools["preview_line_mutation"](
        path="bad_range.py", start_line=10, end_line=20, new_text="x"
    )
    assert not result.success


def test_get_symbol_line_range(tools, tmp_path):
    f = tmp_path / "symbols.py"
    f.write_text(
        'class Engine:\n'
        '    def run(self):\n'
        '        return 1\n'
        '\n'
        'def helper():\n'
        '    return 2\n'
    )
    result = tools["get_symbol_line_range"](path="symbols.py", symbol_name="Engine")
    assert result.success
    assert result.payload["start_line"] == 1
    assert result.payload["end_line"] == 3


def test_get_symbol_line_range_not_found(tools, tmp_path):
    f = tmp_path / "no_symbol.py"
    f.write_text('x = 1\n')
    result = tools["get_symbol_line_range"](path="no_symbol.py", symbol_name="Missing")
    assert not result.success
