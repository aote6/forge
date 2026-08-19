"""Intent-based semantic tools for LLM tool-loop.

Mutations go IntentExecutor → Veritas commit → Projection.
require_confirm=False so the agent loop is not blocked waiting for chat confirm.

Auto-registration: first write to a disk path with no World object creates one
(path@state0 + content@state1), updates ObjectPathMap, then applies the edit.
"""
from __future__ import annotations

from pathlib import Path as PathLib

from forge.adapters.base import ToolResult
from forge.intents.intent import Intent
from forge.intents.executor import IntentExecutor
from forge.projections.base import ProjectionManager
from forge.world.types import Receipt, TransactionDelta
from forge.tools.display import format_block, snippet_around
from forge.tools.tx_shadow import record_tx, undo_last as shadow_undo_last
from forge.tools.project_memory import update_memory


def _format_projection_results(results) -> str:
    if not results:
        return ""
    lines = []
    for r in results:
        mark = "ok" if r.success else "FAIL"
        lines.append(f"  projection[{r.name}]: {mark} {r.reason}")
    return "\n".join(lines)


def _norm_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


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


def _attach_diff(result: ToolResult, path: str, old: str, new: str, tool: str = "edit") -> ToolResult:
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
    clip = {
        "task": f"{tool} path={path}",
        "tx": pl.get("tx_id"),
        "summary": f"edited {path}",
        "undo": "undo_last_tx()",
    }
    result.display = format_block(tool, "OK", kv, body, hint=hint, clip=clip)
    pl["diff"] = diff
    pl["before_snippet"] = before
    pl["after_snippet"] = after
    result.payload = pl
    return result


def _attach_next(result: ToolResult, paths: list[str] | None = None) -> ToolResult:
    if result.success and result.display is not None:
        if "NEXT:" not in result.display:
            result.display = result.display.rstrip() + _next_hint(paths)
    return result


def make_intent_tools(executor: IntentExecutor, projections: ProjectionManager) -> dict:
    """Build semantic tool callables bound to IntentExecutor + ProjectionManager."""

    world = executor._world

    def _sync_path_map(delta) -> None:
        if world is None or delta is None:
            return
        if hasattr(world, "_update_path_map"):
            try:
                world._update_path_map(delta)
                return
            except Exception:
                pass
        path_map = getattr(world, "_path_map", None)
        if path_map is not None and hasattr(path_map, "update_from_delta"):
            path_map.update_from_delta(delta)

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
        _sync_path_map(delta)

        created = list(delta.objects_created) if delta.objects_created else []
        oid = created[0] if created else intent.parameters.get("_created_object_id")
        if oid is None:
            raise RuntimeError("auto-register create_file produced no ObjectId")
        oid = int(oid)
        path_map = getattr(world, "_path_map", None)
        if path_map is not None and hasattr(path_map, "set"):
            path_map.set(oid, path_n)
        return oid, receipt, delta

    def _write_content_to_world(path: str, content: str, oid: int | None) -> ToolResult:
        from forge.core.edit_contract import authoring_to_machine_ops

        path_n = _norm_path(path)
        registered_now = False

        if oid is None:
            # Auto-register: create World object for this path with target content
            try:
                oid, reg_receipt, reg_delta = _register_path(path_n, content)
                registered_now = reg_receipt is not None
                # Registration already wrote full content via create_file — done
                if registered_now:
                    proj = ""
                    if reg_delta is not None:
                        # projection already applied inside _register_path
                        pass
                    return ToolResult.ok(
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
        _sync_path_map(delta)
        proj = _format_projection_results(results)
        return ToolResult.ok(
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
            path_n = _norm_path(path)
            payload = content if content != "" else "\n"
            intent = Intent.create_file(path=path_n, content=payload, require_confirm=False)
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            _sync_path_map(delta)
            created = list(delta.objects_created) if delta.objects_created else []
            oid = created[0] if created else None
            if oid is not None:
                pm = getattr(world, "_path_map", None)
                if pm is not None and hasattr(pm, "set"):
                    pm.set(int(oid), path_n)
            proj = _format_projection_results(results)
            oid_part = f" object_id={oid}" if oid is not None else ""
            return _attach_next(
                ToolResult.ok(
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
                ),
                [path_n],
            )
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
            _sync_path_map(delta)
            proj = _format_projection_results(results)
            return _attach_next(
                ToolResult.ok(
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
                ),
                [path_n],
            )
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
            _sync_path_map(delta)
            proj = _format_projection_results(results)
            summary = ", ".join(f"{r['path']}#{r['object_id']}" for r in resolved)
            return _attach_next(
                ToolResult.ok(
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
                ),
                [r["path"] for r in resolved],
            )
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
            _sync_path_map(delta)
            return ToolResult.ok(
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
                    f"建议: list_world_objects 查看有效 ID。"
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
                return ToolResult.fail(
                    display=(
                        f"str_replace failed: old_string 在 {path_n} 中未找到\n"
                        f"建议: read_file / read_function 核对原文，确保 old_string 完全一致"
                        f"（含缩进与换行）。可先复制文件中的精确片段。"
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

            oid = _resolve_oid(world, path_n, None)
            result = _write_content_to_world(path_n, new_text, oid)
            if result.success:
                reps = n if replace_all else 1
                reg = result.payload.get("registered")
                result.display = (
                    f"RESULT: path={path_n} replacements={reps} "
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
                        update_memory(_project_root(world), recent_files=path_n)
                    except Exception:
                        pass
                result = _attach_diff(result, path_n, text, new_text, tool="str_replace")
                result = _attach_next(result, [path_n])
            return result
        except Exception as e:
            return ToolResult.fail(
                display=f"str_replace failed: {e}\n建议: read_file 后重试；检查 path。"
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
                mode = "overwrite" if oid is not None else "create_or_register"
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
                        update_memory(_project_root(world), recent_files=path_n)
                    except Exception:
                        pass
                result = _attach_diff(result, path_n, old_content, new_content, tool="write_file")
                result = _attach_next(result, [path_n])
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

            if intents:
                receipt, delta = executor.execute_batch(intents)
                projections.project(receipt, delta)
                _sync_path_map(delta)
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
            return _attach_next(
                ToolResult.ok(
                    display=display,
                    payload={
                        "tx_id": tx_id,
                        "version": version,
                        "files": results_meta,
                        "mutation": True,
                        "requires_confirmation": False,
                        "diff": "\n\n".join(diff_blocks) if diff_blocks else "",
                    },
                ),
                paths_out,
            )
        except Exception as e:
            return ToolResult.fail(
                display=f"apply_patch failed: {e}\n建议: 校验 diff；或改用 str_replace。"
            )


    def undo_last_tx() -> ToolResult:
        """Undo last recorded mutation via file shadow (MVP)."""
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
            return ToolResult.ok(
                display=format_block(
                    "undo_last_tx",
                    "OK",
                    {
                        "undone_tx": info.get("undone_tx"),
                        "restored_version": info.get("restored_version"),
                        "paths": ",".join(info.get("paths") or []),
                        "mode": info.get("mode"),
                    },
                    body="已从 shadow 恢复文件内容。",
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
