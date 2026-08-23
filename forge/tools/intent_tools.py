"""Intent-based semantic tools for LLM tool-loop.

Mutations go IntentExecutor → Veritas commit → Projection.
require_confirm=False so the agent loop is not blocked waiting for chat confirm.

Auto-registration: first write to a disk path with no World object creates one
(path@state0 + content@state1), updates ObjectPathMap, then applies the edit.
"""
from __future__ import annotations
import sys

from pathlib import Path as PathLib

from forge.adapters.base import ToolResult
from forge.intents.intent import Intent
from forge.intents.executor import IntentExecutor
from forge.projections.base import ProjectionManager
from forge.world.types import Receipt, TransactionDelta
from forge.tools.display import format_block, snippet_around
from forge.tools.tx_shadow import record_tx, undo_last as shadow_undo_last
from forge.tools.project_memory import update_memory
from forge.tools.related_tests import format_related_hint, symbols_from_edit
from forge.tools.near_miss import (
    find_near_misses,
    diagnose_mismatch,
    suggest_old_string,
    find_occurrence_lines,
)
from forge.tools.errors import decorate_fail_message
from forge.tools.read_cache import invalidate as cache_invalidate
from forge.tools.session_changes import record as record_session_change
from forge.tools.direct_disk import (
    MODE_DIRECT_DISK,
    next_tx_id as next_direct_tx_id,
    world_available,
    write_text as direct_write_text,
)


def _projection_warnings(results) -> list[str]:
    """Collect non-fatal warnings from successful projection results.

    mark_disk_synced 失败时磁盘已写但同步水位未推进：success 保持 True，
    但必须可观测，避免 agent 误以为已 IN_SYNC。
    """
    if not results:
        return []
    out = []
    for r in results:
        w = getattr(r, "warning", None)
        if w:
            out.append(f"{getattr(r, 'name', '?')}: {w}")
    return out


def _format_projection_results(results) -> str:
    """Summarize projections with explicit world/disk status."""
    if not results:
        return "world=ok disk=unknown (no projection results)"
    lines = []
    all_ok = True
    disk_ok = True
    for r in results:
        mark = "ok" if r.success else "FAIL"
        if not r.success:
            all_ok = False
            if "file" in str(getattr(r, "name", "")).lower():
                disk_ok = False
        lines.append(f"  projection[{r.name}]: {mark} {r.reason}")
    world = "ok"
    disk = "ok" if all_ok else ("FAIL" if not disk_ok else "partial")
    out = f"world={world} disk={disk}\n" + "\n".join(lines)
    warnings = _projection_warnings(results)
    if warnings:
        out += "\nSIDE_EFFECT_WARN: " + "; ".join(warnings)
    return out


def _failed_projections(results) -> list:
    """Return projection results with success=False (empty if all ok / no results)."""
    if not results:
        return []
    return [r for r in results if not getattr(r, "success", False)]


def _projection_failure_result(results, receipt=None, *, tool: str = "mutation") -> ToolResult:
    """Build ToolResult.fail when one or more projections failed after World commit.

    World transaction is already committed; disk/host projection may lag. Caller must
    treat this as failure (not success) so the model does not assume files exist.
    """
    failed = _failed_projections(results)
    reasons = []
    for r in failed:
        reasons.append(f"{getattr(r, 'name', '?')}: {getattr(r, 'reason', '') or 'FAIL'}")
    reason_s = "; ".join(reasons) if reasons else "unknown projection failure"
    tx = getattr(receipt, "tx_id", None) if receipt is not None else None
    ver = getattr(receipt, "version", None) if receipt is not None else None
    before = getattr(receipt, "before_root", None) if receipt is not None else None
    after = getattr(receipt, "after_root", None) if receipt is not None else None
    proj = _format_projection_results(results)
    display = (
        f"❌ 事务已提交但投影失败 tool={tool}"
        + (f" tx={tx}" if tx is not None else "")
        + (f" version={ver}" if ver is not None else "")
        + "\n"
        + (f"  before_root={before} after_root={after}\n" if before or after else "")
        + f"{proj}\n"
        f"projection_failed: {reason_s}\n"
        f"世界状态已变更；请依赖 forge_sync 重新对账修复主机投影。"
        f"不要假设磁盘文件已写好。"
    )
    return ToolResult.fail(
        display=display,
        payload={
            "tx_id": tx,
            "version": ver,
            "before_root": before,
            "after_root": after,
            "mutation": True,
            "requires_confirmation": False,
            "projection_failed": True,
            "projection_reasons": reasons,
            "phase": "verifying",
        },
    )


def _norm_path(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


def _resolve_oid(world, path: str, object_id: int | None) -> int | None:
    if object_id is not None:
        return int(object_id)
    if world is None:
        return None
    path_n = _norm_path(path)
    path_map = getattr(world, "_path_map", None)
    if path_map is not None and hasattr(path_map, "find_object_id"):
        oid = path_map.find_object_id(path_n)
        if oid is not None:
            return int(oid)
        oid = path_map.find_object_id(path)
        if oid is not None:
            return int(oid)
    if hasattr(world, "find_object_id_for_path"):
        oid = world.find_object_id_for_path(path_n)
        if oid is not None:
            return int(oid)
    if hasattr(world, "find_object_id"):
        oid = world.find_object_id(path_n)
        if oid is not None:
            return int(oid)
    return None


def _project_root(world) -> str:
    return str(getattr(world, "project_root", None) or ".")


def _read_disk(world, path: str) -> str | None:
    fp = PathLib(_project_root(world)) / path
    if not fp.is_file():
        return None
    try:
        return fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _next_hint(paths: list[str] | None = None) -> str:
    extra = ""
    if paths:
        extra = f"（涉及: {', '.join(paths[:5])}）"
    return (
        f"\nNEXT: 建议 run_test_structured() 或 git_diff() 验证本次修改{extra}"
    )


def _make_unified_diff(path: str, old: str, new: str, max_lines: int = 80) -> str:
    """Build a short unified diff for display (no git required)."""
    import difflib
    a = (old or "").splitlines(keepends=True)
    b = (new or "").splitlines(keepends=True)
    if a == b:
        return ""
    lines = list(
        difflib.unified_diff(
            a, b, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="\n"
        )
    )
    if not lines:
        return ""
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more diff lines)\n"]
    return "".join(lines)


def _attach_diff(
    result: ToolResult,
    path: str,
    old: str,
    new: str,
    tool: str = "edit",
    overwrite_note: str = "",
) -> ToolResult:
    if not result.success:
        return result
    diff = _make_unified_diff(path, old, new)
    before = snippet_around(old or "", max_lines=5)
    after = snippet_around(new or "", max_lines=5)
    body_parts = [
        "--- BEFORE (snippet) ---",
        before,
        "--- AFTER (snippet) ---",
        after,
    ]
    if diff:
        body_parts.extend(["--- DIFF ---", diff.rstrip()])
    body = "\n".join(body_parts)
    pl = dict(result.payload or {})
    kv = {
        "path": path,
        "object_id": pl.get("object_id"),
        "tx": pl.get("tx_id"),
        "version": pl.get("version"),
        "replacements": pl.get("replacements"),
        "mode": pl.get("mode"),
        "registered": pl.get("registered"),
    }
    hint = "不对就 undo_last_tx()；建议 run_test_structured 或 git_diff"
    if overwrite_note:
        hint = overwrite_note + " " + hint
    clip = {
        "task": f"{tool} path={path}",
        "tx": pl.get("tx_id"),
        "summary": f"edited {path}",
        "undo": "undo_last_tx()",
    }
    root = pl.get("_project_root")
    related = ""
    if root and path:
        try:
            syms = list(pl.get("_edit_symbols") or [])
            related = format_related_hint(
                str(root), path, symbol_hint=(syms[0] if syms else None)
            )
        except Exception:
            related = ""
    if related:
        body = body + "\n" + related
    result.display = format_block(tool, "OK", kv, body, hint=hint, clip=clip)
    # P1-4: when related tests exist, force VERIFY_REQUIRED at top of display
    if related and "RELATED_TESTS" in related and "(none found)" not in related:
        target = None
        for line in related.splitlines():
            if "run_test_structured(target=" in line:
                import re as _re
                m = _re.search(r"target=([^)\s]+)", line)
                if m:
                    target = m.group(1).strip().strip("'\"")
                    break
        if not target:
            # fallback: first path-like token after RELATED_TESTS
            for line in related.splitlines():
                if line.startswith("RELATED_TESTS"):
                    parts = line.replace(",", " ").split()
                    for tok in parts:
                        if tok.endswith(".py"):
                            target = tok
                            break
        tgt_expr = repr(target) if target else "'tests/'"
        verify_line = (
            f"VERIFY_REQUIRED: run_test_structured(target={tgt_expr}) "
            f"— 验证完成前不要开始无关重构"
        )
        result.display = verify_line + "\n" + (result.display or "")
        pl["verify_required"] = True
        pl["verify_target"] = target
    pl["diff"] = diff
    pl["before_snippet"] = before
    pl["after_snippet"] = after
    if related:
        pl["related_tests_hint"] = related
    result.payload = pl
    return result


def _attach_direct_disk_note(result: ToolResult) -> ToolResult:
    """P2-1: direct_disk 结果置顶标注模式与 World 未记录的事实。

    `_attach_diff` 会用 format_block 重建 display（kv 渲染成 `mode: direct_disk`），
    所以这里显式再给一行含 `mode=direct_disk` 的说明，保证无论 display 被谁重建，
    模式标识都稳定可见、不被 diff 块淹没。
    """
    if (result.payload or {}).get("mode") != MODE_DIRECT_DISK:
        return result
    if "DIRECT_DISK:" in (result.display or ""):
        return result
    note = (
        f"DIRECT_DISK: mode={MODE_DIRECT_DISK} — veritasd 不可用，已直接写入磁盘；"
        f"本次变更 World 未记录。\n"
        f"恢复 veritasd 后运行 forge_sync 对账；undo_last_tx 仍可回滚本次磁盘修改。"
    )
    result.display = note + "\n" + (result.display or "")
    return result


def _attach_next(result: ToolResult, paths: list[str] | None = None) -> ToolResult:
    if result.success and result.display is not None:
        if "NEXT:" not in result.display:
            result.display = result.display.rstrip() + _next_hint(paths)
    return result


def _note_side_effect_failure(result: ToolResult, name: str, err: BaseException) -> None:
    """主操作已成功时的附属副作用失败：可观测，但不把 success 改成 False。"""
    import sys
    print(f"[forge] side-effect {name} failed: {err}", file=sys.stderr)
    if result.payload is None:
        result.payload = {}
    warns = result.payload.setdefault("side_effect_warnings", [])
    msg = f"{name}: {err}"
    if msg not in warns:
        warns.append(msg)
    if result.success and result.display is not None and "SIDE_EFFECT_WARN:" not in result.display:
        result.display = result.display.rstrip() + f"\nSIDE_EFFECT_WARN: {msg}"


def _attach_warnings(result: ToolResult, warns: list[str]) -> ToolResult:
    """把告警列表挂到 ToolResult payload + display（success 不变）。"""
    if not warns:
        return result
    if result.payload is None:
        result.payload = {}
    existing = result.payload.setdefault("side_effect_warnings", [])
    for w in warns:
        if w not in existing:
            existing.append(w)
    if result.success and result.display is not None and "SIDE_EFFECT_WARN:" not in result.display:
        result.display = result.display.rstrip() + "\nSIDE_EFFECT_WARN: " + "; ".join(warns)
    return result


def _attach_projection_warnings(result: ToolResult, results) -> ToolResult:
    """把投影层的非致命告警（mark_disk_synced 失败等）挂到 ToolResult 上。

    磁盘已写成功，success 保持 True；但同步水位未推进，必须让 agent 看到，
    与 _note_side_effect_failure 同属「成功后的附属失败可观测但不翻转成功」。
    """
    return _attach_warnings(result, _projection_warnings(results))


def make_intent_tools(executor: IntentExecutor, projections: ProjectionManager) -> dict:
    """Build semantic tool callables bound to IntentExecutor + ProjectionManager."""

    world = executor._world

    def _sync_path_map(delta) -> str | None:
        """Best-effort path map sync after a successful World commit.

        Returns an error string if any update attempt failed, else None.
        Does not raise — caller must not turn a successful commit into tool failure.

        注意：`_update_path_map` 失败后仍会尝试 `update_from_delta` 兜底，但
        `_update_path_map` 的失败必须保留在返回值里（即便兜底成功），否则会把
        World 提交成功后的路径映射失败静默吞掉。
        """
        if world is None or delta is None:
            return None
        errors: list[str] = []
        if hasattr(world, "_update_path_map"):
            try:
                world._update_path_map(delta)
            except Exception as e:
                import sys
                print(f"[forge] path_map _update_path_map failed: {e}", file=sys.stderr)
                errors.append(f"_update_path_map: {e}")
        path_map = getattr(world, "_path_map", None)
        if path_map is not None and hasattr(path_map, "update_from_delta"):
            try:
                path_map.update_from_delta(delta)
            except Exception as e:
                import sys
                print(f"[forge] path_map update_from_delta failed: {e}", file=sys.stderr)
                errors.append(f"update_from_delta: {e}")
        return "; ".join(errors) if errors else None

    def _register_path(path: str, content: str) -> tuple[int, Receipt | None, TransactionDelta | None]:
        """Ensure path has a World object. Returns (oid, receipt_or_None, delta_or_None).

        If already mapped, returns (oid, None, None).
        If newly created, returns (oid, receipt, delta) after commit+project.
        """
        path_n = _norm_path(path)
        existing = _resolve_oid(world, path_n, None)
        if existing is not None:
            return existing, None, None

        payload = content if content != "" else "\n"
        intent = Intent.create_file(path=path_n, content=payload, require_confirm=False)
        receipt, delta = executor.execute(intent)
        results = projections.project(receipt, delta)
        failed = _failed_projections(results)
        if failed:
            # World committed but host projection failed — do not advertise oid/path_map.
            reasons = "; ".join(
                f"{getattr(r, 'name', '?')}: {getattr(r, 'reason', '') or 'FAIL'}"
                for r in failed
            )
            raise RuntimeError(
                f"auto-register projection failed after commit "
                f"tx={getattr(receipt, 'tx_id', None)}: {reasons}. "
                f"Run forge_sync; do not assume disk file exists."
            )
        path_map_err = _sync_path_map(delta)

        # 投影层非致命告警（mark_disk_synced 失败）经 delta.metadata 传回调用方，
        # 避免 auto-register 路径把「磁盘已写但水位未推进」静默吞掉。
        warns = _projection_warnings(results)
        if path_map_err:
            warns.append(path_map_err)
        if warns and delta is not None:
            meta = getattr(delta, "metadata", None)
            if meta is None:
                meta = {}
                try:
                    delta.metadata = meta
                except Exception:
                    meta = None
            if isinstance(meta, dict):
                meta.setdefault("_projection_warnings", []).extend(warns)

        created = list(delta.objects_created) if delta.objects_created else []
        oid = created[0] if created else intent.parameters.get("_created_object_id")
        if oid is None:
            raise RuntimeError("auto-register create_file produced no ObjectId")
        oid = int(oid)
        path_map = getattr(world, "_path_map", None)
        if path_map is not None and hasattr(path_map, "set"):
            path_map.set(oid, path_n)
        return oid, receipt, delta

    def _direct_disk_write(path_n: str, content: str) -> ToolResult:
        """P2-1: veritasd 不可用时的一等直写路径。

        只写磁盘，不产生 World receipt、不动 path_map。调用方（str_replace /
        write_file）随后照常记录 shadow undo 与 session_changes —— 那两者本来
        就只依赖磁盘与本地栈，与 Veritas 无关。
        """
        root = _project_root(world)
        try:
            direct_write_text(root, path_n, content)
        except OSError as e:
            return ToolResult.fail(
                display=(
                    f"direct_disk 写入失败 path={path_n} mode={MODE_DIRECT_DISK}: {e}\n"
                    f"veritasd 不可用，Forge 已退到直写路径，但磁盘写入本身失败。\n"
                    f"建议: 检查路径/父目录/权限；文件未被修改。"
                ),
                payload={
                    "path": path_n,
                    "mode": MODE_DIRECT_DISK,
                    "direct_disk": True,
                    "world_recorded": False,
                    "mutation": False,
                    "requires_confirmation": False,
                },
            )
        tx = next_direct_tx_id()
        return ToolResult.ok(
            display=(
                f"RESULT: path={path_n} mode={MODE_DIRECT_DISK} tx={tx} world=unavailable\n"
                f"Wrote file (direct_disk): {path_n}"
            ),
            payload={
                "path": path_n,
                "object_id": None,
                "tx_id": tx,
                "version": None,
                "mutation": True,
                "registered": False,
                "requires_confirmation": False,
                "mode": MODE_DIRECT_DISK,
                "direct_disk": True,
                "world_recorded": False,
            },
        )

    def _write_content_to_world(path: str, content: str, oid: int | None) -> ToolResult:
        from forge.core.edit_contract import authoring_to_machine_ops

        path_n = _norm_path(path)
        registered_now = False

        # P2-1: Veritas 不可用 → 一等直写路径，而不是硬失败。
        # Veritas 可用时这里不做任何事，下面的 World 事务路径完全不变。
        if not world_available(world):
            return _direct_disk_write(path_n, content)

        if oid is None:
            # Auto-register: create World object for this path with target content
            try:
                oid, reg_receipt, reg_delta = _register_path(path_n, content)
                registered_now = reg_receipt is not None
                # Registration already wrote full content via create_file — done
                if registered_now:
                    result = ToolResult.ok(
                        display=(
                            f"RESULT: path={path_n} object_id={oid} "
                            f"tx={reg_receipt.tx_id} version={reg_receipt.version} registered=true\n"
                            f"Wrote file (auto-registered): {path_n}"
                        ),
                        payload={
                            "path": path_n,
                            "object_id": oid,
                            "tx_id": reg_receipt.tx_id,
                            "version": reg_receipt.version,
                            "mutation": True,
                            "registered": True,
                            "requires_confirmation": False,
                        },
                    )
                    reg_warns = []
                    if reg_delta is not None:
                        reg_warns = (
                            (getattr(reg_delta, "metadata", None) or {}).get(
                                "_projection_warnings"
                            )
                            or []
                        )
                    return _attach_warnings(result, reg_warns)
            except Exception as e:
                return ToolResult.fail(
                    display=(
                        f"auto-register failed for {path_n}: {e}\n"
                        f"建议: 确认 veritasd 在线；path 合法。"
                    )
                )

        # Existing object: full-file replace via modify
        root = _project_root(world)
        fp = PathLib(root) / path_n
        old_lines = 1
        if fp.is_file():
            try:
                old_lines = max(1, len(fp.read_text(encoding="utf-8", errors="replace").splitlines()))
            except OSError:
                old_lines = 1
        machine_ops = authoring_to_machine_ops([{
            "type": "replace",
            "start_line": 1,
            "end_line": old_lines,
            "new_text": content,
        }])
        intent = Intent.modify_file(path=path_n, operations=machine_ops, require_confirm=False)
        intent.parameters["object_id"] = int(oid)
        receipt, delta = executor.execute(intent)
        results = projections.project(receipt, delta)
        if _failed_projections(results):
            return _projection_failure_result(results, receipt, tool="write_file")
        path_map_err = _sync_path_map(delta)
        proj = _format_projection_results(results)
        result = ToolResult.ok(
            display=(
                f"RESULT: path={path_n} object_id={oid} tx={receipt.tx_id} version={receipt.version}\n"
                f"Wrote file: {path_n}\n{proj}"
            ).rstrip(),
            payload={
                "path": path_n,
                "object_id": int(oid),
                "tx_id": receipt.tx_id,
                "version": receipt.version,
                "mutation": True,
                "registered": registered_now,
                "requires_confirmation": False,
            },
        )
        result = _attach_projection_warnings(result, results)
        if path_map_err:
            result = _attach_warnings(result, [path_map_err])
        return result

    def create_object() -> ToolResult:
        try:
            intent = Intent.create_object(require_confirm=False)
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            if _failed_projections(results):
                return _projection_failure_result(results, receipt, tool="create_object")
            created = list(delta.objects_created) if delta.objects_created else []
            oid = created[0] if created else intent.parameters.get("_created_object_id")
            if oid is None:
                return ToolResult.fail(
                    display=(
                        "create_object failed: no ObjectId in delta\n"
                        "建议: world_info / list_world_objects 确认 World 在线。"
                    )
                )
            proj = _format_projection_results(results)
            proj_line = f"\n{proj}" if proj else ""
            result = ToolResult.ok(
                display=(
                    f"RESULT: object_id={oid} tx={receipt.tx_id} version={receipt.version}\n"
                    f"Created world object ObjectId={oid}"
                    f"{proj_line}\n"
                    f"下一步若要 link：link_objects(from_id={oid}, to_id=<目标>, link_type=owns)"
                ),
                payload={
                    "object_id": int(oid),
                    "objects_created": [int(x) for x in created],
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "mutation": True,
                    "requires_confirmation": False,
                    "world_operation": True,
                },
            )
            return _attach_projection_warnings(result, results)
        except Exception as e:
            return ToolResult.fail(
                display=f"create_object failed: {e}\n建议: 检查 veritasd 是否在线。"
            )

    def create_file(path: str, content: str = "") -> ToolResult:
        try:
            path_n = _norm_path(path)
            payload = content if content != "" else "\n"
            intent = Intent.create_file(path=path_n, content=payload, require_confirm=False)
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            if _failed_projections(results):
                return _projection_failure_result(results, receipt, tool="create_file")
            path_map_err = _sync_path_map(delta)
            created = list(delta.objects_created) if delta.objects_created else []
            oid = created[0] if created else None
            if oid is not None:
                pm = getattr(world, "_path_map", None)
                if pm is not None and hasattr(pm, "set"):
                    pm.set(int(oid), path_n)
            proj = _format_projection_results(results)
            oid_part = f" object_id={oid}" if oid is not None else ""
            result = ToolResult.ok(
                display=(
                    f"RESULT: path={path_n}{oid_part} tx={receipt.tx_id} version={receipt.version}\n"
                    f"Created file: {path_n}\n{proj}"
                ).rstrip(),
                payload={
                    "path": path_n,
                    "object_id": int(oid) if oid is not None else None,
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "mutation": True,
                    "requires_confirmation": False,
                },
            )
            result = _attach_projection_warnings(result, results)
            if path_map_err:
                result = _attach_warnings(result, [path_map_err])
            return _attach_next(result, [path_n])
        except Exception as e:
            return ToolResult.fail(
                display=(
                    f"create_file failed: {e}\n"
                    f"建议: 检查路径是否合法；若文件已存在，改用 write_file / str_replace。"
                )
            )

    def modify_file(path: str, operations: list, object_id: int | None = None) -> ToolResult:
        try:
            from forge.core.edit_contract import ensure_machine_ops

            path_n = _norm_path(path)
            oid = _resolve_oid(world, path_n, object_id)
            if oid is None:
                disk = _read_disk(world, path_n)
                if disk is None and object_id is None:
                    return ToolResult.fail(
                        display=(
                            f"modify_file failed: 无法解析 path={path_n} 且磁盘无此文件\n"
                            f"建议: write_file 创建，或检查路径。"
                        )
                    )
                oid, _, _ = _register_path(path_n, disk if disk is not None else "\n")

            machine_ops = ensure_machine_ops(operations)
            intent = Intent.modify_file(path=path_n, operations=machine_ops, require_confirm=False)
            intent.parameters["object_id"] = oid
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            if _failed_projections(results):
                return _projection_failure_result(results, receipt, tool="modify_file")
            path_map_err = _sync_path_map(delta)
            proj = _format_projection_results(results)
            result = ToolResult.ok(
                display=(
                    f"RESULT: path={path_n} object_id={oid} tx={receipt.tx_id} "
                    f"version={receipt.version} ops={len(machine_ops)}\n"
                    f"Modified file: {path_n}\n{proj}"
                ).rstrip(),
                payload={
                    "path": path_n,
                    "object_id": oid,
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "ops": len(machine_ops),
                    "mutation": True,
                    "requires_confirmation": False,
                },
            )
            result = _attach_projection_warnings(result, results)
            if path_map_err:
                result = _attach_warnings(result, [path_map_err])
            return _attach_next(result, [path_n])
        except Exception as e:
            return ToolResult.fail(
                display=(
                    f"modify_file failed: {e}\n"
                    f"建议: 优先 str_replace；或确认 operations 格式。"
                )
            )

    def edit_files_batch(edits: list) -> ToolResult:
        if not edits or not isinstance(edits, list):
            return ToolResult.fail(
                display="edit_files_batch failed: edits 必须是非空列表\n建议: 传入 [{path, operations}, ...]"
            )
        try:
            from forge.core.edit_contract import ensure_machine_ops

            intents = []
            resolved = []
            for i, item in enumerate(edits):
                if not isinstance(item, dict):
                    return ToolResult.fail(display=f"edit_files_batch: edits[{i}] 不是 dict")
                path = item.get("path")
                operations = item.get("operations")
                if not path or operations is None:
                    return ToolResult.fail(
                        display=f"edit_files_batch: edits[{i}] 需要 path 与 operations"
                    )
                path_n = _norm_path(path)
                oid = _resolve_oid(world, path_n, item.get("object_id"))
                if oid is None:
                    disk = _read_disk(world, path_n)
                    if disk is None:
                        return ToolResult.fail(
                            display=(
                                f"edit_files_batch: 无法解析 path={path_n}\n"
                                f"建议: 文件须已存在于磁盘，或先 write_file。"
                            )
                        )
                    oid, _, _ = _register_path(path_n, disk)
                machine_ops = ensure_machine_ops(operations)
                intent = Intent.modify_file(
                    path=path_n, operations=machine_ops, require_confirm=False
                )
                intent.parameters["object_id"] = oid
                intents.append(intent)
                resolved.append({"path": path_n, "object_id": oid, "ops": len(machine_ops)})

            receipt, delta = executor.execute_batch(intents)
            results = projections.project(receipt, delta)
            if _failed_projections(results):
                return _projection_failure_result(results, receipt, tool="edit_files_batch")
            path_map_err = _sync_path_map(delta)
            proj = _format_projection_results(results)
            summary = ", ".join(f"{r['path']}#{r['object_id']}" for r in resolved)
            result = ToolResult.ok(
                display=(
                    f"RESULT: batch={len(resolved)} tx={receipt.tx_id} version={receipt.version}\n"
                    f"Edited: {summary}\n{proj}"
                ).rstrip(),
                payload={
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "edits": resolved,
                    "mutation": True,
                    "requires_confirmation": False,
                },
            )
            result = _attach_projection_warnings(result, results)
            if path_map_err:
                result = _attach_warnings(result, [path_map_err])
            return _attach_next(result, [r["path"] for r in resolved])
        except Exception as e:
            return ToolResult.fail(
                display=(
                    f"edit_files_batch failed: {e}\n"
                    f"建议: 拆成多次 str_replace / modify_file。"
                )
            )

    def delete_file(object_id: int | None = None, path: str = "") -> ToolResult:
        try:
            path_n = _norm_path(path) if path else ""
            oid = object_id
            if oid is None and path_n:
                oid = _resolve_oid(world, path_n, None)
            if oid is None:
                return ToolResult.fail(
                    display=(
                        "delete_file failed: 需要 object_id 或可解析的 path\n"
                        "建议: resolve_path_object(path)。"
                    )
                )
            intent = Intent.delete_file(path=path_n or "", require_confirm=False)
            intent.parameters["object_id"] = int(oid)
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            if _failed_projections(results):
                return _projection_failure_result(results, receipt, tool="delete_file")
            path_map_err = _sync_path_map(delta)
            result = ToolResult.ok(
                display=(
                    f"RESULT: object_id={oid} path={path_n} tx={receipt.tx_id} version={receipt.version}\n"
                    f"Deleted object {oid}\n"
                    f"{_format_projection_results(results)}"
                ).rstrip(),
                payload={
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "object_id": int(oid),
                    "path": path_n,
                    "mutation": True,
                    "requires_confirmation": False,
                },
            )
            result = _attach_projection_warnings(result, results)
            if path_map_err:
                result = _attach_warnings(result, [path_map_err])
            return result
        except Exception as e:
            return ToolResult.fail(display=f"delete_file failed: {e}")

    def link_objects(from_id: int, to_id: int, link_type: str = "owns") -> ToolResult:
        try:
            intent = Intent.link_objects(from_id=from_id, to_id=to_id, link_type=link_type)
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            if _failed_projections(results):
                return _projection_failure_result(results, receipt, tool="link_objects")
            proj = _format_projection_results(results)
            proj_line = f"\n{proj}" if proj else ""
            result = ToolResult.ok(
                display=(
                    f"RESULT: from_id={from_id} to_id={to_id} link_type={link_type} "
                    f"tx={receipt.tx_id} version={receipt.version}\n"
                    f"Linked ObjectId={from_id} -[{link_type}]-> ObjectId={to_id}"
                    f"{proj_line}"
                ),
                payload={
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "from_id": int(from_id),
                    "to_id": int(to_id),
                    "link_type": link_type,
                    "mutation": True,
                    "requires_confirmation": False,
                    "world_operation": True,
                },
            )
            return _attach_projection_warnings(result, results)
        except Exception as e:
            return ToolResult.fail(
                display=(
                    f"link_objects failed: {e}\n"
                    f"可能原因: from_id/to_id 不存在或类型非法。\n"
                    f"建议: list_world_objects 查看有效 ID。"
                )
            )

    def unlink_objects(from_id: int, to_id: int) -> ToolResult:
        try:
            intent = Intent.unlink_objects(from_id=from_id, to_id=to_id)
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            if _failed_projections(results):
                return _projection_failure_result(results, receipt, tool="unlink_objects")
            result = ToolResult.ok(
                display=(
                    f"RESULT: unlinked from_id={from_id} to_id={to_id} tx={receipt.tx_id}\n"
                    f"{_format_projection_results(results)}"
                ).rstrip(),
                payload={
                    "tx_id": receipt.tx_id,
                    "from_id": int(from_id),
                    "to_id": int(to_id),
                    "mutation": True,
                    "requires_confirmation": False,
                },
            )
            return _attach_projection_warnings(result, results)
        except Exception as e:
            return ToolResult.fail(display=f"unlink_objects failed: {e}")

    def str_replace(
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> ToolResult:
        """Exact string replace with auto-registration for disk-only files."""
        try:
            path_n = _norm_path(path)
            text = _read_disk(world, path_n)
            if text is None:
                return ToolResult.fail(
                    display=(
                        f"str_replace failed: 文件不存在 {path_n}\n"
                        f"建议: write_file 创建，或 glob_files / search_code 确认路径。"
                    )
                )
            # Tolerate leading/trailing whitespace on old_string from the model
            needle = old_string.strip("\n\r")
            if needle != old_string and needle and needle in text:
                old_string = needle
            n = text.count(old_string)
            if n == 0:
                needle2 = old_string.strip()
                if needle2 and needle2 in text:
                    old_string = needle2
                    n = text.count(old_string)
            if n == 0:
                body_parts = ["old_string 未找到"]
                diag = diagnose_mismatch(text, old_string)
                if diag:
                    kinds = ", ".join(diag.get("kinds") or [])
                    body_parts.append(f"差异类型: {kinds}")
                    if diag.get("hint"):
                        body_parts.append(f"提示: {diag['hint']}")
                    if diag.get("match_line"):
                        body_parts.append(f"近似位置: L{diag['match_line']}")
                suggestion = suggest_old_string(text, old_string)
                if suggestion and suggestion.get("text"):
                    body_parts.append(
                        f"--- SUGGESTED old_string (L{suggestion['line']}, 可直接复制) ---\n"
                        f"{suggestion['text']}"
                    )
                misses = find_near_misses(text, old_string)
                if misses:
                    body_parts.append(
                        "--- NEAR_MISS candidates ---\n" + "\n----\n".join(misses)
                    )
                body = "\n".join(body_parts)
                hint = (
                    "使用 SUGGESTED old_string 原样重试；或扩大上下文使匹配唯一"
                    if suggestion
                    else "复制 NEAR_MISS 片段作为 old_string，或 read_function 后再改"
                )
                return ToolResult.fail(
                    display=format_block(
                        "str_replace",
                        "FAIL",
                        {"path": path_n, "reason": "old_string not found"},
                        body,
                        hint=hint,
                    )
                )
            if n > 1 and not replace_all:
                occ_lines = find_occurrence_lines(text, old_string)[:3]
                lines_txt = ", ".join(f"L{ln}" for ln in occ_lines) if occ_lines else "(未知)"
                return ToolResult.fail(
                    display=(
                        f"str_replace failed: old_string 出现 {n} 次，拒绝歧义替换\n"
                        f"命中行号(前3处): {lines_txt}\n"
                        f"建议: 扩大 old_string 上下文使其唯一，或设 replace_all=true。"
                    )
                )
            new_text = (
                text.replace(old_string, new_string)
                if replace_all
                else text.replace(old_string, new_string, 1)
            )

            oid = _resolve_oid(world, path_n, None)
            result = _write_content_to_world(path_n, new_text, oid)
            if result.success:
                reps = n if replace_all else 1
                reg = result.payload.get("registered")
                mode_v = result.payload.get("mode")
                mode_part = f" mode={mode_v}" if mode_v else ""
                result.display = (
                    f"RESULT: path={path_n} replacements={reps}{mode_part} "
                    f"object_id={result.payload.get('object_id')} "
                    f"tx={result.payload.get('tx_id')} version={result.payload.get('version')}"
                    f"{' registered=true' if reg else ''}\n"
                    f"str_replace ok: {path_n}"
                )
                if result.success and result.payload:
                    try:
                        record_tx(
                            _project_root(world),
                            result.payload.get("tx_id"),
                            result.payload.get("version"),
                            {path_n: text},
                        )
                        update_memory(_project_root(world), recent_files=path_n, last_status="edited")
                        cache_invalidate(_project_root(world), path_n)
                    except Exception as e:
                        _note_side_effect_failure(result, "record_tx/memory/cache", e)
                if result.payload is not None:
                    result.payload["_project_root"] = _project_root(world)
                    result.payload["replacements"] = (n if replace_all else 1)
                if result.payload is not None:
                    result.payload["_edit_symbols"] = symbols_from_edit(text, new_text)
                result = _attach_diff(result, path_n, text, new_text, tool="str_replace")
                try:
                    record_session_change(
                        path_n,
                        tool="str_replace",
                        tx_id=(result.payload or {}).get("tx_id"),
                        summary=(
                            f"replacements={(result.payload or {}).get('replacements')}"
                            + (f" mode={mode_v}" if mode_v else "")
                        ),
                        project_root=_project_root(world),
                    )
                except Exception as e:
                    _note_side_effect_failure(result, "record_session_change", e)
                result = _attach_next(result, [path_n])
                # display 被 _attach_diff 重建；把 payload 里的投影告警重新挂回 display。
                result = _attach_warnings(
                    result, list((result.payload or {}).get("side_effect_warnings", []))
                )
                result = _attach_direct_disk_note(result)
            return result
        except Exception as e:
            return ToolResult.fail(
                display=decorate_fail_message(format_block("str_replace","FAIL",{"reason":str(e)}), e)
            )

    def write_file(path: str, content: str = "") -> ToolResult:
        """Create or overwrite entire file; auto-registers disk paths into World."""
        try:
            path_n = _norm_path(path)
            oid = _resolve_oid(world, path_n, None)
            old_content = _read_disk(world, path_n) or ""
            new_content = content if content is not None else ""
            result = _write_content_to_world(path_n, new_content, oid)
            if result.success:
                # direct_disk 路径已在 payload 里定了 mode；World 路径沿用原判定。
                mode = (result.payload or {}).get("mode") or (
                    "overwrite" if oid is not None else "create_or_register"
                )
                if result.payload is not None:
                    result.payload["mode"] = mode
                overwrite_note = ""
                if old_content.strip() and old_content != new_content:
                    old_lines = old_content.count("\n") + 1
                    overwrite_note = (
                        f"覆盖了已存在文件({old_lines}行)；"
                        f"若只想改部分内容，下次可用 str_replace/modify_file 更安全。"
                    )
                result.display = (
                    f"RESULT: path={path_n} mode={mode} object_id={result.payload.get('object_id')} "
                    f"tx={result.payload.get('tx_id')} version={result.payload.get('version')}\n"
                    f"write_file ok: {path_n}"
                )
                if result.success and result.payload:
                    try:
                        record_tx(
                            _project_root(world),
                            result.payload.get("tx_id"),
                            result.payload.get("version"),
                            {path_n: old_content},
                        )
                        update_memory(_project_root(world), recent_files=path_n, last_status="edited")
                        cache_invalidate(_project_root(world), path_n)
                    except Exception as e:
                        _note_side_effect_failure(result, "record_tx/memory/cache", e)
                if result.payload is not None:
                    result.payload["_project_root"] = _project_root(world)
                if result.payload is not None:
                    result.payload["_edit_symbols"] = symbols_from_edit(old_content, new_content)
                result = _attach_diff(
                    result, path_n, old_content, new_content,
                    tool="write_file", overwrite_note=overwrite_note,
                )
                try:
                    record_session_change(
                        path_n,
                        tool="write_file",
                        tx_id=(result.payload or {}).get("tx_id"),
                        summary=f"write_file mode={mode}",
                        project_root=_project_root(world),
                    )
                except Exception as e:
                    _note_side_effect_failure(result, "record_session_change", e)
                result = _attach_next(result, [path_n])
                # display 被 _attach_diff 重建；把 payload 里的投影告警重新挂回 display。
                result = _attach_warnings(
                    result, list((result.payload or {}).get("side_effect_warnings", []))
                )
                result = _attach_direct_disk_note(result)
            return result
        except Exception as e:
            return ToolResult.fail(
                display=(
                    f"write_file failed: {e}\n"
                    f"建议: 检查路径是否正确，父目录是否存在；确认 veritasd 在线。"
                )
            )

    def apply_patch(patch: str) -> ToolResult:
        """Apply a unified diff in one Veritas transaction (multi-file)."""
        try:
            from forge.tools.patch_utils import apply_unified_patch_to_files

            root = _project_root(world)
            plan = apply_unified_patch_to_files(root, patch)
            if plan.get("error"):
                return ToolResult.fail(
                    display=(
                        f"apply_patch failed: {plan['error']}\n"
                        f"建议: 检查 unified diff 格式（--- / +++ / @@）。"
                    )
                )
            results_meta = []
            results: list = []
            # Apply each file via _write_content_to_world sequentially but we want one tx
            # Prefer batch: register all, then execute_batch of modify intents
            from forge.core.edit_contract import authoring_to_machine_ops

            intents = []
            paths_out = []
            for item in plan["files"]:
                path_n = _norm_path(item["path"])
                new_content = item["new_content"]
                oid = _resolve_oid(world, path_n, None)
                if oid is None:
                    # register with NEW content in one create (covers new + existing disk)
                    oid, reg_receipt, _ = _register_path(path_n, new_content)
                    results_meta.append({
                        "path": path_n,
                        "object_id": oid,
                        "mode": "register",
                        "tx_id": getattr(reg_receipt, "tx_id", None),
                    })
                    paths_out.append(path_n)
                    continue
                fp = PathLib(root) / path_n
                old_lines = 1
                if fp.is_file():
                    try:
                        old_lines = max(
                            1,
                            len(fp.read_text(encoding="utf-8", errors="replace").splitlines()),
                        )
                    except OSError:
                        old_lines = 1
                machine_ops = authoring_to_machine_ops([{
                    "type": "replace",
                    "start_line": 1,
                    "end_line": old_lines,
                    "new_text": new_content,
                }])
                intent = Intent.modify_file(
                    path=path_n, operations=machine_ops, require_confirm=False
                )
                intent.parameters["object_id"] = oid
                intents.append(intent)
                results_meta.append({"path": path_n, "object_id": oid, "mode": "modify"})
                paths_out.append(path_n)

            path_map_err = None
            if intents:
                receipt, delta = executor.execute_batch(intents)
                results = projections.project(receipt, delta)
                if _failed_projections(results):
                    return _projection_failure_result(results, receipt, tool="apply_patch")
                path_map_err = _sync_path_map(delta)
                tx_id, version = receipt.tx_id, receipt.version
            else:
                # all were register-only creates
                tx_id = results_meta[-1].get("tx_id") if results_meta else None
                version = None

            summary = ", ".join(f"{m['path']}" for m in results_meta)
            diff_blocks = []
            for item in plan.get("files") or []:
                path_n = _norm_path(item["path"])
                d = _make_unified_diff(
                    path_n, item.get("old_content", ""), item.get("new_content", "")
                )
                if d:
                    diff_blocks.append(d.rstrip())
            display = (
                f"RESULT: apply_patch files={len(results_meta)} tx={tx_id} version={version}\n"
                f"Patched: {summary}"
            )
            if diff_blocks:
                display = display + "\nDIFF:\n" + "\n\n".join(diff_blocks)
            result = ToolResult.ok(
                display=display,
                payload={
                    "tx_id": tx_id,
                    "version": version,
                    "files": results_meta,
                    "mutation": True,
                    "requires_confirmation": False,
                    "diff": "\n\n".join(diff_blocks) if diff_blocks else "",
                },
            )
            result = _attach_projection_warnings(result, results)
            if path_map_err:
                result = _attach_warnings(result, [path_map_err])
            return _attach_next(result, paths_out)
        except Exception as e:
            return ToolResult.fail(
                display=f"apply_patch failed: {e}\n建议: 校验 diff；或改用 str_replace。"
            )


    def undo_last_tx() -> ToolResult:
        """Undo last mutation via file shadow; invalidate caches."""
        try:
            root = _project_root(world)
            info = shadow_undo_last(root)
            if not info.get("ok"):
                return ToolResult.fail(
                    display=format_block(
                        "undo_last_tx",
                        "FAIL",
                        {"reason": info.get("error")},
                        hint="没有可撤销的事务；先成功 str_replace/write_file 一次。",
                    )
                )
            for path in info.get("paths") or []:
                try:
                    cache_invalidate(root, path)
                except Exception as e:
                    print(f"[undo_last_tx] cache_invalidate failed for {path}: {e}", file=sys.stderr)
            try:
                record_session_change(
                    ",".join(info.get("paths") or []) or "(undo)",
                    tool="undo_last_tx",
                    tx_id=info.get("undone_tx"),
                    summary="shadow revert",
                    project_root=root,
                )
            except Exception as e:
                print(f"[undo_last_tx] record_session_change failed: {e}", file=sys.stderr)
            body = (
                "已从 shadow 恢复磁盘文件。\n"
                "说明: mode=file_shadow_revert；World 账本可能仍较新，以磁盘 read 为准。"
            )
            return ToolResult.ok(
                display=format_block(
                    "undo_last_tx",
                    "OK",
                    {
                        "undone_tx": info.get("undone_tx"),
                        "restored_version": info.get("restored_version"),
                        "paths": ",".join(info.get("paths") or []),
                        "mode": info.get("mode"),
                        "world": "may_lag",
                        "disk": "restored",
                    },
                    body,
                    hint="可继续编辑或 run_test_structured",
                    clip={"undo_tx": info.get("undone_tx"), "paths": ",".join(info.get("paths") or [])},
                ),
                payload=info,
            )
        except Exception as e:
            return ToolResult.fail(
                display=format_block("undo_last_tx", "FAIL", {"reason": str(e)})
            )


    return {
        "undo_last_tx": undo_last_tx,
        "str_replace": str_replace,
        "write_file": write_file,
        "create_object": create_object,
        "create_file": create_file,
        "modify_file": modify_file,
        "edit_files_batch": edit_files_batch,
        "apply_patch": apply_patch,
        "delete_file": delete_file,
        "link_objects": link_objects,
        "unlink_objects": unlink_objects,
    }
