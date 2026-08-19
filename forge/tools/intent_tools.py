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


    def _full_file_ops(content: str) -> list:
        """Single machine op that replaces entire file content."""
        from forge.core.edit_contract import authoring_to_machine_ops
        # authoring: 1-based inclusive end for "all lines" — use large end or compute
        lines = content.splitlines(keepends=True)
        if content and not content.endswith("\n") and lines:
            # preserve no final newline semantics via new_text
            pass
        return authoring_to_machine_ops([{
            "type": "replace",
            "start_line": 1,
            "end_line": max(1, len(content.splitlines()) or 1),
            "new_text": content,
        }])

    def _write_content_to_world(path: str, content: str, oid: int | None) -> ToolResult:
        from forge.core.edit_contract import authoring_to_machine_ops
        if oid is None:
            # create_file validator rejects empty string content
            payload_content = content if content != "" else "\n"
            intent = Intent.create_file(path=path, content=payload_content, require_confirm=False)
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            created = list(delta.objects_created) if delta.objects_created else []
            new_oid = created[0] if created else None
            return ToolResult.ok(
                display=(
                    f"RESULT: path={path} object_id={new_oid} tx={receipt.tx_id} version={receipt.version}\n"
                    f"Wrote file (create): {path}"
                ),
                payload={
                    "path": path,
                    "object_id": int(new_oid) if new_oid is not None else None,
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "mutation": True,
                    "requires_confirmation": False,
                },
            )
        # Full-file replace via one authoring op spanning whole file
        # end_line: existing file line count; new_text is full content
        root = getattr(world, "project_root", None) or "."
        from pathlib import Path as P
        fp = P(root) / path
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
        intent = Intent.modify_file(path=path, operations=machine_ops, require_confirm=False)
        intent.parameters["object_id"] = int(oid)
        receipt, delta = executor.execute(intent)
        results = projections.project(receipt, delta)
        proj = _format_projection_results(results)
        return ToolResult.ok(
            display=(
                f"RESULT: path={path} object_id={oid} tx={receipt.tx_id} version={receipt.version}\n"
                f"Wrote file: {path}\n{proj}"
            ).rstrip(),
            payload={
                "path": path,
                "object_id": int(oid),
                "tx_id": receipt.tx_id,
                "version": receipt.version,
                "mutation": True,
                "requires_confirmation": False,
            },
        )

    def str_replace(
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> ToolResult:
        """Exact string replace — primary edit tool (Claude Code Edit style)."""
        try:
            from pathlib import Path as P
            root = getattr(world, "project_root", None) or "."
            fp = P(root) / path
            if not fp.is_file():
                return ToolResult.fail(
                    display=(
                        f"str_replace failed: 文件不存在 {path}\n"
                        f"建议: 用 write_file 创建，或 glob_files / search_code 确认路径。"
                    )
                )
            text = fp.read_text(encoding="utf-8", errors="replace")
            n = text.count(old_string)
            if n == 0:
                return ToolResult.fail(
                    display=(
                        f"str_replace failed: old_string 在 {path} 中未找到\n"
                        f"建议: read_file / read_function 核对原文（须完全一致，含空白）。"
                    )
                )
            if n > 1 and not replace_all:
                return ToolResult.fail(
                    display=(
                        f"str_replace failed: old_string 出现 {n} 次，拒绝歧义替换\n"
                        f"建议: 扩大 old_string 上下文使其唯一，或设 replace_all=true。"
                    )
                )
            new_text = (
                text.replace(old_string, new_string)
                if replace_all
                else text.replace(old_string, new_string, 1)
            )
            oid = _resolve_oid(world, path, None)
            result = _write_content_to_world(path, new_text, oid)
            if result.success:
                result.display = (
                    f"RESULT: path={path} replacements={n if replace_all else 1} "
                    f"object_id={result.payload.get('object_id')} "
                    f"tx={result.payload.get('tx_id')} version={result.payload.get('version')}\n"
                    f"str_replace ok: {path}"
                )
            return result
        except Exception as e:
            return ToolResult.fail(
                display=f"str_replace failed: {e}\n建议: read_file 后重试；检查 path 与权限。"
            )

    def write_file(path: str, content: str = "") -> ToolResult:
        """Create or overwrite entire file content."""
        try:
            oid = _resolve_oid(world, path, None)
            from pathlib import Path as P
            root = getattr(world, "project_root", None) or "."
            exists = (P(root) / path).is_file()
            if oid is None and exists:
                # on disk but not in world — create_file may fail if projection expects new
                # still try create; if fails, surface error
                pass
            result = _write_content_to_world(path, content if content is not None else "", oid)
            if result.success:
                mode = "overwrite" if oid is not None else "create"
                result.display = (
                    f"RESULT: path={path} mode={mode} object_id={result.payload.get('object_id')} "
                    f"tx={result.payload.get('tx_id')} version={result.payload.get('version')}\n"
                    f"write_file ok: {path}"
                )
            return result
        except Exception as e:
            return ToolResult.fail(
                display=(
                    f"write_file failed: {e}\n"
                    f"建议: 检查路径；若对象状态异常，resolve_path_object 后重试。"
                )
            )


    return {
        "str_replace": str_replace,
        "write_file": write_file,
        "create_object": create_object,
        "create_file": create_file,
        "modify_file": modify_file,
        "edit_files_batch": edit_files_batch,
        "delete_file": delete_file,
        "link_objects": link_objects,
        "unlink_objects": unlink_objects,
    }
