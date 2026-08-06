"""Intent-based semantic tools for LLM.

These tools expose semantic operations (CreateFile, ModifyFile, etc.)
to the LLM. They route through IntentExecutor then ProjectionManager.
LLM never sees Veritas primitives (world_*).
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


def make_intent_tools(executor: IntentExecutor, projections: ProjectionManager) -> dict:
    """Build semantic tool callables bound to IntentExecutor + ProjectionManager."""

    def _commit_and_project() -> tuple[Receipt, TransactionDelta, list]:
        receipt, delta = executor._world.commit_session()
        results = projections.project(receipt, delta)
        return receipt, delta, results

    def create_file(path: str, content: str = "") -> ToolResult:
        try:
            intent = Intent.create_file(path=path, content=content, require_confirm=True)
            if intent.policy.get("require_confirm", True):
                delta = executor.stage(intent)
                preview = executor.execute_dry_run(intent)
                return ToolResult.ok(
                    display=(
                        f"⏸️ 已准备创建文件: {path}\n"
                        f"预览: {preview.get('content_preview', '')[:200]}\n"
                        f"---\n请输入「确认」提交，或「取消」放弃。"
                    ),
                    payload={
                        "requires_confirmation": True,
                        "mutation": True,
                        "phase": "wait_confirm",
                        "path": path,
                        "preview": preview,
                        "objects_created": delta.objects_created,
                    },
                )
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            return ToolResult.ok(
                display=(
                    f"Created file: {path}\n"
                    f"tx={receipt.tx_id} version={receipt.version}\n"
                    f"{_format_projection_results(results)}"
                ),
                payload={
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "path": path,
                    "objects_created": delta.objects_created,
                    "mutation": True,
                    "requires_confirmation": False,
                },
            )
        except Exception as e:
            return ToolResult.fail(display=f"create_file failed: {e}")

    def modify_file(path: str, operations: list, object_id: int) -> ToolResult:
        try:
            intent = Intent.modify_file(path=path, operations=operations, require_confirm=True)
            intent.parameters["object_id"] = object_id
            if intent.policy.get("require_confirm", True):
                delta = executor.stage(intent)
                return ToolResult.ok(
                    display=(
                        f"⏸️ 已准备修改文件: {path} (object={object_id})\n"
                        f"---\n请输入「确认」提交，或「取消」放弃。"
                    ),
                    payload={
                        "requires_confirmation": True,
                        "mutation": True,
                        "phase": "wait_confirm",
                        "path": path,
                        "object_id": object_id,
                    },
                )
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            return ToolResult.ok(
                display=(
                    f"Modified file: {path}\n"
                    f"tx={receipt.tx_id} version={receipt.version}\n"
                    f"{_format_projection_results(results)}"
                ),
                payload={
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "path": path,
                    "mutation": True,
                    "requires_confirmation": False,
                },
            )
        except Exception as e:
            return ToolResult.fail(display=f"modify_file failed: {e}")

    def delete_file(object_id: int, path: str = "") -> ToolResult:
        try:
            intent = Intent.delete_file(path=path, require_confirm=True)
            intent.parameters["object_id"] = object_id
            if intent.policy.get("require_confirm", True):
                executor.stage(intent)
                return ToolResult.ok(
                    display=(
                        f"⏸️ 已准备删除 object {object_id}\n"
                        f"---\n请输入「确认」提交，或「取消」放弃。"
                    ),
                    payload={
                        "requires_confirmation": True,
                        "mutation": True,
                        "phase": "wait_confirm",
                        "object_id": object_id,
                    },
                )
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            return ToolResult.ok(
                display=(
                    f"Deleted object {object_id}, tx={receipt.tx_id}\n"
                    f"{_format_projection_results(results)}"
                ),
                payload={
                    "tx_id": receipt.tx_id,
                    "object_id": object_id,
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
            return ToolResult.ok(
                display=(
                    f"Linked {from_id} -[{link_type}]-> {to_id}, tx={receipt.tx_id}\n"
                    f"{_format_projection_results(results)}"
                ),
                payload={"tx_id": receipt.tx_id, "mutation": True, "requires_confirmation": False},
            )
        except Exception as e:
            return ToolResult.fail(display=f"link_objects failed: {e}")

    def unlink_objects(from_id: int, to_id: int) -> ToolResult:
        try:
            intent = Intent.unlink_objects(from_id=from_id, to_id=to_id)
            receipt, delta = executor.execute(intent)
            results = projections.project(receipt, delta)
            return ToolResult.ok(
                display=(
                    f"Unlinked {from_id} -> {to_id}, tx={receipt.tx_id}\n"
                    f"{_format_projection_results(results)}"
                ),
                payload={"tx_id": receipt.tx_id, "mutation": True, "requires_confirmation": False},
            )
        except Exception as e:
            return ToolResult.fail(display=f"unlink_objects failed: {e}")

    return {
        "create_file": create_file,
        "modify_file": modify_file,
        "delete_file": delete_file,
        "link_objects": link_objects,
        "unlink_objects": unlink_objects,
    }
