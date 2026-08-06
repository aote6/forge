"""Intent-based semantic tools for LLM.

These tools expose semantic operations (CreateFile, ModifyFile, etc.)
to the LLM. They internally route through IntentExecutor.
LLM never sees Veritas primitives (world_*).
"""

from __future__ import annotations

from forge.adapters.base import ToolResult
from forge.intents.intent import Intent
from forge.intents.executor import IntentExecutor


def make_intent_tools(executor: IntentExecutor) -> dict:
    """Build semantic tool callables bound to an IntentExecutor."""

    def create_file(path: str, content: str = "") -> ToolResult:
        try:
            intent = Intent.create_file(path=path, content=content)
            receipt, delta = executor.execute(intent)
            return ToolResult.ok(
                display=(
                    f"Created file: {path}\n"
                    f"tx={receipt.tx_id} version={receipt.version}"
                ),
                payload={
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "path": path,
                    "objects_created": delta.objects_created,
                },
            )
        except Exception as e:
            return ToolResult.fail(display=f"create_file failed: {e}")

    def modify_file(path: str, operations: list, object_id: int) -> ToolResult:
        try:
            intent = Intent.modify_file(path=path, operations=operations)
            intent.parameters["object_id"] = object_id
            receipt, delta = executor.execute(intent)
            return ToolResult.ok(
                display=(
                    f"Modified file: {path}\n"
                    f"tx={receipt.tx_id} version={receipt.version}"
                ),
                payload={
                    "tx_id": receipt.tx_id,
                    "version": receipt.version,
                    "path": path,
                },
            )
        except Exception as e:
            return ToolResult.fail(display=f"modify_file failed: {e}")

    def delete_file(object_id: int) -> ToolResult:
        try:
            intent = Intent.delete_file(path="")
            intent.parameters["object_id"] = object_id
            receipt, delta = executor.execute(intent)
            return ToolResult.ok(
                display=f"Deleted object {object_id}, tx={receipt.tx_id}",
                payload={"tx_id": receipt.tx_id, "object_id": object_id},
            )
        except Exception as e:
            return ToolResult.fail(display=f"delete_file failed: {e}")

    def link_objects(from_id: int, to_id: int, link_type: str = "owns") -> ToolResult:
        try:
            intent = Intent.link_objects(from_id=from_id, to_id=to_id, link_type=link_type)
            receipt, delta = executor.execute(intent)
            return ToolResult.ok(
                display=f"Linked {from_id} -[{link_type}]-> {to_id}, tx={receipt.tx_id}",
                payload={"tx_id": receipt.tx_id},
            )
        except Exception as e:
            return ToolResult.fail(display=f"link_objects failed: {e}")

    def unlink_objects(from_id: int, to_id: int) -> ToolResult:
        try:
            intent = Intent.unlink_objects(from_id=from_id, to_id=to_id)
            receipt, delta = executor.execute(intent)
            return ToolResult.ok(
                display=f"Unlinked {from_id} -> {to_id}, tx={receipt.tx_id}",
                payload={"tx_id": receipt.tx_id},
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
