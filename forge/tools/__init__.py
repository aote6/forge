"""工具组装入口。

生产 Runtime 使用 allow_mutation=True：注册只读 + mutation 工具，
突变经 IntentExecutor → WorldSession → Veritas（commit/abort）再投影。
allow_mutation=False 时仅只读（兼容旧调用）。
confirm 成功条件 = commit 成功 + 全部 projection 成功。
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
    sync_layer=None,
):
    """Assemble tool callables for Runtime tool-loops.

    allow_mutation=False (default): only local read/discovery tools.
    allow_mutation=True: also register intent mutation tools (create_object,
    create_file, link_objects, ...). Production Runtime uses allow_mutation=True;
    mutations stay transactional via IntentExecutor + Veritas.
    """
    tools = make_local_tools(workspace, safe_mode=safe_mode, world_runtime=world_runtime)
    confirm_fn = None
    abort_fn = None

    if allow_mutation and world_runtime is not None and projections is not None:
        executor = IntentExecutor(world_runtime)
        tools.update(make_intent_tools(executor, projections))

        def confirm_fn() -> ToolResult:
            """Commit staged session then project. Success requires all projections ok.

            Veritas commit is not rolled back on projection failure (world is
            authoritative). Caller must treat failure as divergence and recover
            via forge_sync (Sync Layer) using receipt evidence in the payload.
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
                            f"世界状态已变更；请依赖 forge_sync 重新对账修复主机投影。"
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

    if sync_layer is not None:
        from forge.sync.sync_layer import FAST_FORWARD_DISK_TO_WORLD, IN_SYNC

        def forge_sync() -> ToolResult:
            """显式同步：IN_SYNC 无操作 / FAST_FORWARD 安全推进 / CONFLICT 停止并出 diff。

            报告同时承载同步状态；只读状态查询可用 Runtime.sync_status() 或 CLI `status`。
            """
            try:
                # P2-1c 清账：先看是否正处在 Disk → World 分叉；若 forge_sync 成功把它
                # FAST_FORWARD 回 World，则清掉对应的 direct_disk 待对账标记。
                try:
                    was_disk_to_world = (
                        sync_layer.detect().status == FAST_FORWARD_DISK_TO_WORLD
                    )
                except Exception:
                    was_disk_to_world = False
                report = sync_layer.sync()
                ok = report.status == IN_SYNC
                payload = {"mutation": True, **report.to_dict()}
                display = report.format()
                if ok and was_disk_to_world:
                    from forge.tools.session_changes import clear_pending_direct_disk

                    clear_pending_direct_disk(sync_layer.project_root)
                # P2-1c: 列出 direct_disk 待对账文件，提醒用户这些磁盘变更已被/需被对账。
                # 只提示，不在这里额外做任何 fast-forward（对账方向仍由 report 决定）。
                from forge.tools.session_changes import pending_direct_disk

                pending = pending_direct_disk(sync_layer.project_root)
                if pending:
                    paths = []
                    seen = set()
                    for e in pending:
                        p = str(e.get("path") or "").strip()
                        if p and p not in seen:
                            seen.add(p)
                            paths.append(p)
                    if paths:
                        display += (
                            "\n\ndirect_disk 待对账文件（veritasd 不可达期间直写，World 未记录）：\n"
                            + "\n".join(f"- {p}" for p in paths[:20])
                            + "\n请确认 forge_sync 已将这些磁盘变更纳入 World（必要时显式决策方向）。"
                        )
                if ok:
                    return ToolResult.ok(display=display, payload=payload)
                return ToolResult.fail(
                    display=(
                        display
                        + "\n建议: CONFLICT 时请明确决定以 World 还是 Disk/Git 为准，勿自动覆盖。"
                    ),
                    payload=payload,
                )
            except Exception as e:
                return ToolResult.fail(display=f"forge_sync failed: {e}")

        tools["forge_sync"] = forge_sync

    return tools, confirm_fn, abort_fn
