"""Intent-based semantic tools for LLM tool-loop.

Mutations go IntentExecutor → Veritas commit → Projection.
require_confirm=False so the agent loop is not blocked waiting for chat confirm.
"""
from __future__ import annotations

from forge.adapters.base import ToolResult
from forge.intents.intent import Intent
from forge.intents.executor import IntentExecutor
from forge.projections.base import ProjectionManager
from forge.world.types import Receipt, TransactionDelta


def _format_projection_results(results) -> str:
    if not results:
        return ""
    lines = []
    for r in results:
        mark = "ok" if r.success else "FAIL"
        lines.append(f"  projection[{r.name}]: {mark} {r.reason}")
    return "\n".join(lines)


def _resolve_oid(world, path: str, object_id: int | None) -> int | None:
    if object_id is not None:
        return int(object_id)
    if world is None:
        return None
    path_n = path.replace("\\", "/").lstrip("./")
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
    return None


def make_intent_tools(executor: IntentExecutor, projections: ProjectionManager) -> dict:
    """Build semantic tool callables bound to IntentExecutor + ProjectionManager."""

    world = executor._world

    def create_object() -> ToolResult:
        try:
            intent = Intent.create_object(require_confirm=False)
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
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
            return ToolResult.ok(
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
        except Exception as e:
            return ToolResult.fail(
                display=f"create_object failed: {e}\n建议: 检查 veritasd 是否在线。"
            )

    def create_file(path: str, content: str = "") -> ToolResult:
        try:
            intent = Intent.create_file(path=path, content=content, require_confirm=False)
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            created = list(delta.objects_created) if delta.objects_created else []
            oid = created[0] if created else None
            proj = _format_projection_results(results)
            oid_part = f" object_id={oid}" if oid is not None else ""
            return ToolResult.ok(
                display=(
                    f"RESULT: path={path}{oid_part} tx={receipt.tx_id} version={receipt.version}\n"
                    f"Created file: {path}\n"
                    f"{proj}"
                ).rstrip(),
                payload={
                    "path": path,
                    "object_id": int(oid) if oid is not None else None,
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "mutation": True,
                    "requires_confirmation": False,
                },
            )
        except Exception as e:
            return ToolResult.fail(
                display=(
                    f"create_file failed: {e}\n"
                    f"建议: 检查路径是否合法；若文件已存在，改用 modify_file。"
                )
            )

    def modify_file(path: str, operations: list, object_id: int | None = None) -> ToolResult:
        """修改文件。object_id 可省略，将通过 path 自动解析。operations 可含多处修改。"""
        try:
            from forge.core.edit_contract import ensure_machine_ops

            oid = _resolve_oid(world, path, object_id)
            if oid is None:
                return ToolResult.fail(
                    display=(
                        f"modify_file failed: 无法解析 path={path} 的 ObjectId\n"
                        f"可能原因: 文件未进入 World。\n"
                        f"建议: resolve_path_object('{path}') 或先 create_file。"
                    )
                )
            machine_ops = ensure_machine_ops(operations)
            intent = Intent.modify_file(path=path, operations=machine_ops, require_confirm=False)
            intent.parameters["object_id"] = oid
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            proj = _format_projection_results(results)
            return ToolResult.ok(
                display=(
                    f"RESULT: path={path} object_id={oid} tx={receipt.tx_id} version={receipt.version} "
                    f"ops={len(machine_ops)}\n"
                    f"Modified file: {path}\n"
                    f"{proj}"
                ).rstrip(),
                payload={
                    "path": path,
                    "object_id": oid,
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "ops": len(machine_ops),
                    "mutation": True,
                    "requires_confirmation": False,
                },
            )
        except Exception as e:
            return ToolResult.fail(
                display=(
                    f"modify_file failed: {e}\n"
                    f"建议: 确认 operations 为 machine 格式 "
                    f"(start_line/end_line 0-based half-open + new_lines)，"
                    f"或用 preview_line_mutation 先预览。"
                )
            )

    def edit_files_batch(edits: list) -> ToolResult:
        """批量修改多个文件，在同一 Veritas 事务中提交。

        edits: [{path, operations, object_id?}, ...]
        """
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
                oid = _resolve_oid(world, path, item.get("object_id"))
                if oid is None:
                    return ToolResult.fail(
                        display=(
                            f"edit_files_batch: 无法解析 path={path}\n"
                            f"建议: resolve_path_object 或先 create_file。"
                        )
                    )
                machine_ops = ensure_machine_ops(operations)
                intent = Intent.modify_file(
                    path=path, operations=machine_ops, require_confirm=False
                )
                intent.parameters["object_id"] = oid
                intents.append(intent)
                resolved.append({"path": path, "object_id": oid, "ops": len(machine_ops)})

            receipt, delta = executor.execute_batch(intents)
            results = projections.project(receipt, delta)
            proj = _format_projection_results(results)
            summary = ", ".join(f"{r['path']}#{r['object_id']}" for r in resolved)
            return ToolResult.ok(
                display=(
                    f"RESULT: batch={len(resolved)} tx={receipt.tx_id} version={receipt.version}\n"
                    f"Edited: {summary}\n"
                    f"{proj}"
                ).rstrip(),
                payload={
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "edits": resolved,
                    "mutation": True,
                    "requires_confirmation": False,
                },
            )
        except Exception as e:
            return ToolResult.fail(
                display=(
                    f"edit_files_batch failed: {e}\n"
                    f"建议: 拆成多次 modify_file；确认每个 path 已有 ObjectId。"
                )
            )

    def delete_file(object_id: int | None = None, path: str = "") -> ToolResult:
        try:
            oid = object_id
            if oid is None and path:
                oid = _resolve_oid(world, path, None)
            if oid is None:
                return ToolResult.fail(
                    display=(
                        "delete_file failed: 需要 object_id 或可解析的 path\n"
                        "建议: resolve_path_object(path) 获取 ID。"
                    )
                )
            intent = Intent.delete_file(path=path or "", require_confirm=False)
            intent.parameters["object_id"] = int(oid)
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            return ToolResult.ok(
                display=(
                    f"RESULT: object_id={oid} path={path} tx={receipt.tx_id} version={receipt.version}\n"
                    f"Deleted object {oid}\n"
                    f"{_format_projection_results(results)}"
                ).rstrip(),
                payload={
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "object_id": int(oid),
                    "path": path,
                    "mutation": True,
                    "requires_confirmation": False,
                },
            )
        except Exception as e:
            return ToolResult.fail(display=f"delete_file failed: {e}")

    def link_objects(from_id: int, to_id: int, link_type: str = "owns") -> ToolResult:
        try:
            intent = Intent.link_objects(from_id=from_id, to_id=to_id, link_type=link_type)
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            proj = _format_projection_results(results)
            proj_line = f"\n{proj}" if proj else ""
            return ToolResult.ok(
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
        except Exception as e:
            return ToolResult.fail(
                display=(
                    f"link_objects failed: {e}\n"
                    f"可能原因: from_id/to_id 不存在或类型非法。\n"
                    f"建议: list_world_objects 查看有效 ID；link_type ∈ owns|depends_on|references。"
                )
            )

    def unlink_objects(from_id: int, to_id: int) -> ToolResult:
        try:
            intent = Intent.unlink_objects(from_id=from_id, to_id=to_id)
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            return ToolResult.ok(
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
        except Exception as e:
            return ToolResult.fail(display=f"unlink_objects failed: {e}")

    return {
        "create_object": create_object,
        "create_file": create_file,
        "modify_file": modify_file,
        "edit_files_batch": edit_files_batch,
        "delete_file": delete_file,
        "link_objects": link_objects,
        "unlink_objects": unlink_objects,
    }
