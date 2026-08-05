"""Veritas 工具 —— Forge 控制平面的眼睛"""
from forge.adapters.base import ToolResult
from forge.veritas_client import VeritasClient


def make_veritas_tools(client: VeritasClient):

    def veritas_list_objects() -> ToolResult:
        """列出 Veritas Kernel 中所有 Object"""
        try:
            objs = client.list_objects()
            if not objs:
                return ToolResult.ok(display="（世界为空，无 Object）")
            lines = [f"  {o.object_id:<8} {o.state}" for o in objs]
            return ToolResult.ok(
                display="Object ID  State\n" + "\n".join(lines),
                payload={"objects": [{"id": o.object_id, "state": o.state} for o in objs]}
            )
        except Exception as e:
            return ToolResult.fail(display=f"查询失败: {e}")

    def veritas_get_object(object_id: int) -> ToolResult:
        """查询指定 Object 的状态"""
        try:
            state = client.get_object_state(object_id)
            if state is None:
                return ToolResult.fail(display=f"Object {object_id} 不存在")
            return ToolResult.ok(
                display=f"Object {object_id}: {state}",
                payload={"object_id": object_id, "state": state}
            )
        except Exception as e:
            return ToolResult.fail(display=f"查询失败: {e}")

    def veritas_object_exists(object_id: int) -> ToolResult:
        """检查 Object 是否存在"""
        try:
            exists = client.object_exists(object_id)
            return ToolResult.ok(
                display=f"Object {object_id}: {'存在' if exists else '不存在'}",
                payload={"object_id": object_id, "exists": exists}
            )
        except Exception as e:
            return ToolResult.fail(display=f"查询失败: {e}")

    return {
        "veritas_list_objects": veritas_list_objects,
        "veritas_get_object": veritas_get_object,
        "veritas_object_exists": veritas_object_exists,
    }
