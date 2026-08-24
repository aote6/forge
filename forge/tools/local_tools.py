"""本地只读与命令工具（不涉及世界事务）。

本模块保留 make_local_tools 组装入口，并再导出共享 helper 以保持向后兼容。
具体工具实现按功能拆分到：
  - read_tools.py     文件读取 / 内容类
  - search_tools.py   搜索 / 符号导航
  - git_tools.py      Git 查询
  - test_tools.py     测试 / 诊断
  - world_tools.py    World（Veritas）查询
  - meta_tools.py     todo / web_fetch / 命令执行 / Mastodon / 状态
"""

from __future__ import annotations

from forge.tools._common import (
    LOG_PATH,
    MAX_OUTPUT_CHARS,
    _log,
    _truncate,
    _truncate_head,
)
from forge.tools.read_tools import make_read_tools
from forge.tools.search_tools import make_search_tools
from forge.tools.git_tools import make_git_tools
from forge.tools.test_tools import make_test_tools
from forge.tools.world_tools import make_world_tools
from forge.tools.meta_tools import make_meta_tools

__all__ = [
    "make_local_tools",
    "MAX_OUTPUT_CHARS",
    "LOG_PATH",
    "_log",
    "_truncate",
    "_truncate_head",
]


def make_local_tools(workspace, world_runtime=None) -> dict:
    """组装所有只读/查询工具。按功能分组后合并，返回的 key 集合与原实现一致。"""
    tools: dict = {}
    tools.update(make_read_tools(workspace))
    tools.update(make_search_tools(workspace))
    tools.update(make_git_tools(workspace))
    tools.update(make_test_tools(workspace))
    tools.update(make_world_tools(workspace, world_runtime))
    tools.update(make_meta_tools(workspace))
    return tools
