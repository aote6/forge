"""工具组装入口。

P1-A: conversation / legacy 默认只注册只读工具。
      mutation 必须经 EngineeringOrchestrator → ExecutionAdapter。
P1-B: confirm 成功条件 = commit 成功 + 全部 projection 成功。
"""
from __future__ import annotations

from forge.adapters.base import ToolResult
from forge.tools.local_tools import make_local_tools
from forge.tools.intent_tools import make_intent_tools
from forge.intents.executor import IntentExecutor


def make_tools(
    workspace,
    safe_mode: str = "blacklist",
    world_runtime=None,
    projections=None,
    *,
    allow_mutation: bool = False,
):
    """Assemble tool callables for Runtime tool-loops.

    allow_mutation=False (default): only local read/discovery tools.
    allow_mutation=True: also register intent mutation tools (tests / explicit opt-in only).
    Production Runtime must keep allow_mutation=False so all mutations go through
    EngineeringOrchestrator.
    """
    tools = make_local_tools(workspace, safe_mode=safe_mode)
    confirm_fn = None
    abort_fn = None

    if allow_mutation and world_runtime is not None and projections is not None:
        executor = IntentExecutor(world_runtime)
        tools.update(make_intent_tools(executor, projections))

        def confirm_fn() -> ToolResult:
            """Commit staged session then project. Success requires all projections ok.

            Veritas commit is not rolled back on projection failure (world is
            authoritative). Caller must treat failure as divergence and recover
            via ProjectionRecovery using receipt evidence in the payload.
            """
            try:
                if world_runtime.current_session is None:
                    return ToolResult.fail(display="没有待确认的事务。")
                receipt, delta = world_runtime.commit_session()
                results = projections.project(receipt, delta)
                parts = []
                failed = []
                for r in results or []:
                    mark = "ok" if getattr(r, "success", False) else "FAIL"
                    reason = getattr(r, "reason", "") or ""
                    parts.append(f"  projection[{r.name}]: {mark} {reason}".rstrip())
                    if not getattr(r, "success", False):
                        failed.append(r)
                proj_lines = "\n" + "\n".join(parts) if parts else ""
                evidence = {
                    "tx_id": getattr(receipt, "tx_id", None),
                    "version": getattr(receipt, "version", None),
                    "before_root": getattr(receipt, "before_root", None),
                    "after_root": getattr(receipt, "after_root", None),
                    "mutation": True,
                    "requires_confirmation": False,
                    "phase": "verifying",
                    "projection_failed": bool(failed),
                    "projection_reasons": [
                        getattr(r, "reason", "") or r.name for r in failed
                    ],
                }
                if failed:
                    reasons = "; ".join(
                        getattr(r, "reason", "") or r.name for r in failed
                    )
                    return ToolResult.fail(
                        display=(
                            f"❌ 事务已提交但投影失败 tx={receipt.tx_id} "
                            f"version={receipt.version}\n"
                            f"  before_root={receipt.before_root} "
                            f"after_root={receipt.after_root}"
                            f"{proj_lines}\n"
                            f"projection_failed: {reasons}\n"
                            f"世界状态已变更；请依赖 ProjectionRecovery 修复主机投影。"
                        ),
                        payload=evidence,
                    )
                return ToolResult.ok(
                    display=(
                        f"✅ 已提交 tx={receipt.tx_id} version={receipt.version}\n"
                        f"  before_root={receipt.before_root} "
                        f"after_root={receipt.after_root}"
                        f"{proj_lines}"
                    ),
                    payload=evidence,
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
