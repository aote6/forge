"""工具组装入口。

拆分：
- make_local_tools: 只读与命令
- make_intent_tools: 语义 Intent（LLM 唯一变更入口）
- make_tools: 组合以上，并装配 Projection；返回 (tools, confirm_fn, abort_fn)
  confirm/abort 回调供 Runtime 使用，Runtime 不直接依赖 Projection。
"""

from __future__ import annotations

from forge.adapters.base import ToolResult
from forge.tools.local_tools import make_local_tools
from forge.tools.intent_tools import make_intent_tools
from forge.intents.executor import IntentExecutor
from forge.projections.base import ProjectionManager
from forge.projections.file_projection import FileProjection
from forge.projections.git_projection import GitProjection
from forge.projections.index_projection import IndexProjection


def make_tools(workspace, safe_mode: str = "blacklist", world_runtime=None):
    tools = make_local_tools(workspace, safe_mode=safe_mode)
    confirm_fn = None
    abort_fn = None

    if world_runtime is not None:
        projections = ProjectionManager()
        fp = FileProjection(project_root=workspace.project_root)
        if hasattr(world_runtime, "_path_map"):
            fp.object_path_map = world_runtime._path_map
        projections.register(fp)
        projections.register(GitProjection(project_root=workspace.project_root))
        projections.register(IndexProjection(project_root=workspace.project_root))
        executor = IntentExecutor(world_runtime)
        tools.update(make_intent_tools(executor, projections))

        def confirm_fn() -> ToolResult:
            try:
                if world_runtime.current_session is None:
                    return ToolResult.fail(display="没有待确认的事务。")
                receipt, delta = world_runtime.commit_session()
                results = projections.project(receipt, delta)
                parts = []
                for r in results:
                    mark = "ok" if r.success else "FAIL"
                    parts.append(f"  projection[{r.name}]: {mark} {r.reason}")
                proj_lines = "\n" + "\n".join(parts) if parts else ""
                return ToolResult.ok(
                    display=(
                        f"✅ 已提交 tx={receipt.tx_id} version={receipt.version}\n"
                        f"  before_root={receipt.before_root} after_root={receipt.after_root}"
                        f"{proj_lines}"
                    ),
                    payload={
                        "tx_id": receipt.tx_id,
                        "version": receipt.version,
                        "mutation": True,
                        "requires_confirmation": False,
                        "phase": "verifying",
                    },
                )
            except Exception as e:
                return ToolResult.fail(display=f"提交失败: {e}")

        def abort_fn() -> ToolResult:
            try:
                world_runtime.abort_session()
                return ToolResult.ok(display="事务已取消。", payload={"mutation": False})
            except Exception as e:
                return ToolResult.fail(display=f"取消失败: {e}")

    return tools, confirm_fn, abort_fn
