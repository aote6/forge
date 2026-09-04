"""工具组装入口。

生产 Runtime 使用 allow_mutation=True：注册只读 + mutation 工具，
突变经 IntentExecutor → WorldSession → Veritas（commit/abort）再投影。
allow_mutation=False 时仅只读（兼容旧调用）。
"""
from __future__ import annotations

from forge.adapters.base import ToolResult
from forge.tools.local_tools import make_local_tools
from forge.tools.intent_tools import make_intent_tools
from forge.intents.executor import IntentExecutor


def make_tools(
    workspace,
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
    tools = make_local_tools(workspace, world_runtime=world_runtime)
    if allow_mutation and world_runtime is not None and projections is not None:
        executor = IntentExecutor(world_runtime)
        tools.update(make_intent_tools(executor, projections))

    if sync_layer is not None:
        from forge.sync.sync_layer import FAST_FORWARD_DISK_TO_WORLD, IN_SYNC

        def forge_sync() -> ToolResult:
            """显式同步：IN_SYNC 无操作 / FAST_FORWARD 安全推进 / CONFLICT 停止并出 diff。

            报告同时承载同步状态；只读状态查询可用 Runtime.sync_status() 或 CLI `status`。
            R2: RuntimeState.pending.kind==sync_decision 时拒绝推进。
            Phase A: DECIDED + applicable → 返回已授权未执行，不推进 SyncState / 磁盘。
            """
            try:
                from forge.runtime_state import sync_decision_pending_blocks
                from forge.sync.decision import (
                    ALREADY_IN_SYNC,
                    APPLICABLE,
                    LEGACY_NO_GENERATION,
                    PARTIAL_EXECUTION,
                    STATUS_DECIDED,
                    STALE,
                    SyncDecisionStore,
                    classify_decision_applicability,
                    supersede_decided_with_pending,
                )

                blocked, summary = sync_decision_pending_blocks(sync_layer.project_root)
                if blocked:
                    return ToolResult.fail(
                        display=(
                            "⛔ SyncDecision pending：同步策略点尚未决议，已拒绝 forge_sync 推进。\n"
                            f"pending: {summary}\n"
                            "请先 resolve_sync_decision(direction=disk_to_world|world_to_disk|abort)。"
                        )
                    )

                # Phase D：先处理可能存在的 IN_PROGRESS reconcile attempt
                import pathlib as _pathlib
                from forge.sync.attempt import ReconcileAttemptStore, recover as recover_attempt

                attempt_store = ReconcileAttemptStore(
                    _pathlib.Path(sync_layer.project_root) / ".forge"
                )
                recovery = recover_attempt(
                    attempt_store, _pathlib.Path(sync_layer.project_root)
                )
                if recovery.action == "stopped":
                    payload = {
                        "mutation": False,
                        "protocol": "sync_decision_reconciliation",
                        "phase": "D",
                        "decision_status": "recovery_blocked",
                        "reconciliation_authorized": False,
                        "execution_pending": False,
                        "reason": recovery.reason,
                        "mismatched_path": recovery.mismatched_path,
                        "expected": recovery.expected,
                        "actual": recovery.actual,
                    }
                    return ToolResult.fail(
                        display=(
                            "⛔ Phase D recovery blocked：崩溃恢复时磁盘状态不匹配，"
                            "无法证明当前 receipt 的 expected effect。\n"
                            f"reason: {recovery.reason}\n"
                            f"mismatched_path: {recovery.mismatched_path}\n"
                            f"expected: {recovery.expected}\n"
                            f"actual: {recovery.actual}\n"
                            "禁止 supersede，禁止继续执行。需要人工检查。"
                        ),
                        payload=payload,
                    )
                if recovery.action == "backfilled_and_ready" and recovery.attempt is not None:
                    # 边界 receipt 已写盘但 mark 未落：补 mark，并用
                    # attempt 里冻结的 expected effects 重建 baseline hash
                    attempt = recovery.attempt
                    idx = attempt.next_receipt_index
                    if idx > 0 and idx <= len(attempt.execution_receipts):
                        boundary = attempt.execution_receipts[idx - 1]
                        ver = None
                        if isinstance(boundary, dict):
                            ver = boundary.get("version")
                        else:
                            ver = getattr(boundary, "version", None)

                        written_paths: list[str] = []
                        deleted_paths: list[str] = []
                        expected = attempt.expected_effect_at(idx - 1)
                        if isinstance(expected, dict):
                            for entry in expected.get("written_paths") or []:
                                pth = entry.get("path") if isinstance(entry, dict) else str(entry)
                                if pth:
                                    written_paths.append(str(pth))
                            for pth in expected.get("deleted_paths") or []:
                                if pth:
                                    deleted_paths.append(str(pth))

                        if ver is not None:
                            try:
                                sync_layer.state.mark_disk_synced(
                                    int(ver),
                                    written_paths=written_paths,
                                    deleted_paths=deleted_paths,
                                    source="user_reconcile_world_wins",
                                )
                            except Exception as e:
                                import sys as _sys
                                print(f"[phase_d] backfill mark failed: {e}", file=_sys.stderr)

                # Phase A：DECIDED 协议分支（在 sync() 之前；只读 detect，不碰 SyncState 写入）
                report = sync_layer.detect()
                decision_store = SyncDecisionStore(sync_layer.project_root)
                decision = decision_store.load()
                if decision is not None and decision.status == STATUS_DECIDED:
                    kind = classify_decision_applicability(
                        decision, report, sync_layer.state
                    )
                    if kind == ALREADY_IN_SYNC or report.status == IN_SYNC:
                        decision_store.clear()
                        payload = {
                            "mutation": False,
                            "protocol": "sync_decision_reconciliation",
                            "phase": "A",
                            "decision_status": "cleared",
                            "reconciliation_authorized": False,
                            "execution_pending": False,
                            **report.to_dict(),
                        }
                        return ToolResult.ok(
                            display=report.format()
                            + "\n[phase A] IN_SYNC：已清除 durable SyncDecision。",
                            payload=payload,
                        )
                    if kind == APPLICABLE:
                        from forge.sync.decision import DIRECTION_DISK_TO_WORLD

                        if decision.direction == DIRECTION_DISK_TO_WORLD:
                            # Phase B：preflight+accept_disk_wins 在 SyncLayer；clear 在控制面
                            out = sync_layer.apply_disk_to_world_decision(
                                decision, report
                            )
                            detail = str(getattr(out, "detail", "") or "")
                            if detail.startswith("phase_b:preflight_stale"):
                                old_id = decision.decision_id
                                new_decision = supersede_decided_with_pending(
                                    sync_layer.project_root, report, sync_layer.state
                                )
                                if new_decision is None:
                                    payload = {
                                        "mutation": False,
                                        "protocol": "sync_decision_reconciliation",
                                        "phase": "B",
                                        "decision_status": "stale",
                                        "reconciliation_authorized": False,
                                        "execution_pending": False,
                                        "pending_opened": False,
                                        "reason": "preflight_stale",
                                        "blocked_by": "human_intervention",
                                        "old_decision_id": old_id,
                                        **report.to_dict(),
                                    }
                                    return ToolResult.ok(
                                        display=(
                                            report.format()
                                            + "\n[phase B] preflight stale，但 HI pending 阻止 supersede。"
                                        ),
                                        payload=payload,
                                    )
                                payload = {
                                    "mutation": False,
                                    "protocol": "sync_decision_reconciliation",
                                    "phase": "B",
                                    "decision_status": "stale",
                                    "reconciliation_authorized": False,
                                    "execution_pending": False,
                                    "pending_opened": True,
                                    "reason": "preflight_stale",
                                    "old_decision_id": old_id,
                                    "new_decision_id": new_decision.decision_id,
                                    **report.to_dict(),
                                }
                                return ToolResult.ok(
                                    display=(
                                        report.format()
                                        + "\n[phase B] preflight：观察已偏离 generation，已原子替换为新 PENDING。"
                                        f"\nnew_decision_id={new_decision.decision_id}"
                                    ),
                                    payload=payload,
                                )
                            if out.status == IN_SYNC:
                                decision_store.clear()
                                payload = {
                                    "mutation": True,
                                    "protocol": "sync_decision_reconciliation",
                                    "phase": "B",
                                    "decision_status": "cleared",
                                    "reconciliation_authorized": True,
                                    "execution_pending": False,
                                    "direction": decision.direction,
                                    "decision_id": decision.decision_id,
                                    "last_sync_source": "user_reconcile_disk_wins",
                                    **out.to_dict(),
                                }
                                return ToolResult.ok(
                                    display=(
                                        out.format()
                                        + "\n[phase B] disk_to_world 完成：verify=IN_SYNC，"
                                        "SyncDecision 已清除。"
                                    ),
                                    payload=payload,
                                )
                            # verify 失败：保留 DECIDED，不 supersede
                            payload = {
                                "mutation": True,
                                "protocol": "sync_decision_reconciliation",
                                "phase": "B",
                                "decision_status": "execution_failed",
                                "reconciliation_authorized": True,
                                "execution_pending": False,
                                "direction": decision.direction,
                                "decision_id": decision.decision_id,
                                **out.to_dict(),
                            }
                            return ToolResult.fail(
                                display=(
                                    out.format()
                                    + "\n[phase B] disk_to_world 执行后 verify 未达 IN_SYNC；"
                                    "保留 DECIDED，不自动重新授权。"
                                ),
                                payload=payload,
                            )

                        from forge.sync.decision import DIRECTION_WORLD_TO_DISK as _W2D

                        if decision.direction == _W2D:
                            out = sync_layer.apply_world_to_disk_decision(
                                decision, report
                            )
                            # 持久化 progress（mark_count），防止 partial 被 supersede
                            decision_store.save(decision)
                            detail = str(getattr(out, "detail", "") or "")
                            if detail.startswith("phase_c:preflight_stale"):
                                if int(getattr(decision, "mark_count", 0) or 0) > 0:
                                    payload = {
                                        "mutation": True,
                                        "protocol": "sync_decision_reconciliation",
                                        "phase": "C",
                                        "decision_status": "execution_failed",
                                        "reconciliation_authorized": True,
                                        "execution_pending": False,
                                        "direction": decision.direction,
                                        "decision_id": decision.decision_id,
                                        "mark_count": decision.mark_count,
                                        **out.to_dict(),
                                    }
                                    return ToolResult.fail(
                                        display=(
                                            out.format()
                                            + "\n[phase C] preflight 失败但 mark_count>0；"
                                            "保留 DECIDED，不 supersede（Phase D ownership）。"
                                        ),
                                        payload=payload,
                                    )
                                old_id = decision.decision_id
                                new_decision = supersede_decided_with_pending(
                                    sync_layer.project_root, report, sync_layer.state
                                )
                                if new_decision is None:
                                    payload = {
                                        "mutation": False,
                                        "protocol": "sync_decision_reconciliation",
                                        "phase": "C",
                                        "decision_status": "stale",
                                        "pending_opened": False,
                                        "blocked_by": "human_intervention",
                                        "old_decision_id": old_id,
                                        **report.to_dict(),
                                    }
                                    return ToolResult.ok(
                                        display=report.format()
                                        + "\n[phase C] preflight stale，HI 阻止 supersede。",
                                        payload=payload,
                                    )
                                payload = {
                                    "mutation": False,
                                    "protocol": "sync_decision_reconciliation",
                                    "phase": "C",
                                    "decision_status": "stale",
                                    "pending_opened": True,
                                    "old_decision_id": old_id,
                                    "new_decision_id": new_decision.decision_id,
                                    **report.to_dict(),
                                }
                                return ToolResult.ok(
                                    display=(
                                        report.format()
                                        + "\n[phase C] preflight stale（mark_count=0），已替换为新 PENDING。"
                                    ),
                                    payload=payload,
                                )
                            if out.status == IN_SYNC:
                                decision_store.clear()
                                payload = {
                                    "mutation": True,
                                    "protocol": "sync_decision_reconciliation",
                                    "phase": "C",
                                    "decision_status": "cleared",
                                    "reconciliation_authorized": True,
                                    "execution_pending": False,
                                    "direction": decision.direction,
                                    "decision_id": decision.decision_id,
                                    **out.to_dict(),
                                }
                                return ToolResult.ok(
                                    display=(
                                        out.format()
                                        + "\n[phase C] world_to_disk 完成：verify=IN_SYNC，"
                                        "SyncDecision 已清除。"
                                    ),
                                    payload=payload,
                                )
                            payload = {
                                "mutation": True,
                                "protocol": "sync_decision_reconciliation",
                                "phase": "C",
                                "decision_status": "execution_failed",
                                "reconciliation_authorized": True,
                                "execution_pending": False,
                                "direction": decision.direction,
                                "decision_id": decision.decision_id,
                                "mark_count": int(
                                    getattr(decision, "mark_count", 0) or 0
                                ),
                                **out.to_dict(),
                            }
                            return ToolResult.fail(
                                display=(
                                    out.format()
                                    + "\n[phase C] world_to_disk 未完成；保留 DECIDED"
                                    + (
                                        "（mark_count>0，Phase D ownership）。"
                                        if int(getattr(decision, "mark_count", 0) or 0)
                                        > 0
                                        else "。"
                                    )
                                ),
                                payload=payload,
                            )

                        payload = {
                            "mutation": False,
                            "protocol": "sync_decision_reconciliation",
                            "phase": "A",
                            "decision_status": "authorized_pending_execution",
                            "reconciliation_authorized": True,
                            "execution_pending": True,
                            "direction": decision.direction,
                            "decision_id": decision.decision_id,
                            **report.to_dict(),
                        }
                        return ToolResult.ok(
                            display=(
                                report.format()
                                + "\n[phase A] SyncDecision 已授权且仍 applicable："
                                f"direction={decision.direction} decision_id={decision.decision_id}。"
                            ),
                            payload=payload,
                        )
                    if kind == PARTIAL_EXECUTION:
                        # mark_count>0：不得 supersede；尝试继续或报 execution_failed
                        from forge.sync.decision import DIRECTION_WORLD_TO_DISK as _W2D

                        if decision.direction == _W2D:
                            out = sync_layer.apply_world_to_disk_decision(
                                decision, report
                            )
                            decision_store.save(decision)
                            if out.status == IN_SYNC:
                                decision_store.clear()
                                payload = {
                                    "mutation": True,
                                    "protocol": "sync_decision_reconciliation",
                                    "phase": "C",
                                    "decision_status": "cleared",
                                    **out.to_dict(),
                                }
                                return ToolResult.ok(
                                    display=out.format()
                                    + "\n[phase C] partial 续跑完成 IN_SYNC。",
                                    payload=payload,
                                )
                            payload = {
                                "mutation": True,
                                "protocol": "sync_decision_reconciliation",
                                "phase": "C",
                                "decision_status": "execution_failed",
                                "mark_count": int(
                                    getattr(decision, "mark_count", 0) or 0
                                ),
                                "decision_id": decision.decision_id,
                                **out.to_dict(),
                            }
                            return ToolResult.fail(
                                display=(
                                    out.format()
                                    + "\n[phase C] partial_execution：保留原 DECIDED，"
                                    "不 supersede。"
                                ),
                                payload=payload,
                            )
                        payload = {
                            "mutation": False,
                            "protocol": "sync_decision_reconciliation",
                            "phase": "C",
                            "decision_status": "execution_failed",
                            "mark_count": int(getattr(decision, "mark_count", 0) or 0),
                            "decision_id": decision.decision_id,
                            **report.to_dict(),
                        }
                        return ToolResult.fail(
                            display=(
                                report.format()
                                + "\n[phase C] partial_execution 非 world_to_disk；"
                                "保留 DECIDED。"
                            ),
                            payload=payload,
                        )
                    if kind in (STALE, LEGACY_NO_GENERATION):
                        if int(getattr(decision, "mark_count", 0) or 0) > 0:
                            payload = {
                                "mutation": False,
                                "protocol": "sync_decision_reconciliation",
                                "phase": "C",
                                "decision_status": "execution_failed",
                                "reconciliation_authorized": True,
                                "execution_pending": False,
                                "mark_count": int(decision.mark_count or 0),
                                "decision_id": decision.decision_id,
                                "reason": kind,
                                **report.to_dict(),
                            }
                            return ToolResult.fail(
                                display=(
                                    report.format()
                                    + "\n[phase C] classify="
                                    + kind
                                    + " 但 mark_count>0；禁止 supersede，保留 DECIDED。"
                                ),
                                payload=payload,
                            )
                        old_id = decision.decision_id
                        new_decision = supersede_decided_with_pending(
                            sync_layer.project_root, report, sync_layer.state
                        )
                        if new_decision is None:
                            # HI owns the slot: do not touch SyncDecision artifact.
                            payload = {
                                "mutation": False,
                                "protocol": "sync_decision_reconciliation",
                                "phase": "A",
                                "decision_status": "stale",
                                "reconciliation_authorized": False,
                                "execution_pending": False,
                                "pending_opened": False,
                                "reason": kind,
                                "blocked_by": "human_intervention",
                                "old_decision_id": old_id,
                                **report.to_dict(),
                            }
                            return ToolResult.ok(
                                display=(
                                    report.format()
                                    + f"\n[phase A] SyncDecision stale ({kind})，"
                                    "但 RuntimeState 存在 human_intervention pending；"
                                    "未覆盖 SyncDecision，请先处理 HI。"
                                ),
                                payload=payload,
                            )
                        payload = {
                            "mutation": False,
                            "protocol": "sync_decision_reconciliation",
                            "phase": "A",
                            "decision_status": "stale",
                            "reconciliation_authorized": False,
                            "execution_pending": False,
                            "pending_opened": True,
                            "reason": kind,
                            "old_decision_id": old_id,
                            "new_decision_id": new_decision.decision_id,
                            **report.to_dict(),
                        }
                        return ToolResult.ok(
                            display=(
                                report.format()
                                + f"\n[phase A] SyncDecision stale ({kind})："
                                f"旧 decision_id={old_id} 已原子替换为新 PENDING "
                                f"new_decision_id={new_decision.decision_id}。"
                                "\n请重新 resolve_sync_decision(direction=...)。"
                            ),
                            payload=payload,
                        )

                # P2-1c 清账：先看是否正处在 Disk → World 分叉；若 forge_sync 成功把它
                # FAST_FORWARD 回 World，则清掉对应的 direct_disk 待对账标记。
                try:
                    was_disk_to_world = (
                        report.status == FAST_FORWARD_DISK_TO_WORLD
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

    return tools
