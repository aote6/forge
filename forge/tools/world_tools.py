"""World（Veritas）查询类只读工具。"""

from __future__ import annotations

from forge.adapters.base import ToolResult
from forge.tools._common import _log


def make_world_tools(workspace, world_runtime) -> dict:
    def world_info() -> ToolResult:
        """查看 Veritas 世界摘要：版本、root hash、对象数。"""
        if world_runtime is None:
            return ToolResult.fail(display="World runtime 不可用（veritasd 未启动或未配置）")
        try:
            info = world_runtime.world_info()
            _log("world_info", {}, True)
            return ToolResult.ok(
                display=(
                    f"World version: {info.version}\n"
                    f"State root: {info.state_root}\n"
                    f"Object count: {info.object_count}"
                ),
                payload={"version": info.version, "object_count": info.object_count, "mutation": False},
            )
        except Exception as e:
            _log("world_info", {}, False, str(e))
            return ToolResult.fail(display=f"world_info 失败: {e}")

    def list_world_objects() -> ToolResult:
        """查看 Veritas 世界中的所有对象。"""
        if world_runtime is None:
            return ToolResult.fail(display="World runtime 不可用")
        try:
            objects = world_runtime.list_objects()
            if not objects:
                return ToolResult.ok(display="（世界中暂无对象）", payload={"objects": [], "mutation": False})
            lines = [f"  id={obj.object_id} state={obj.state}" for obj in objects]
            _log("list_world_objects", {}, True)
            return ToolResult.ok(
                display="\n".join(lines),
                payload={"objects": [{"id": o.object_id, "state": o.state} for o in objects], "mutation": False},
            )
        except Exception as e:
            _log("list_world_objects", {}, False, str(e))
            return ToolResult.fail(display=f"list_world_objects 失败: {e}")

    def get_world_object(object_id: int) -> ToolResult:
        """查看指定对象的状态。"""
        if world_runtime is None:
            return ToolResult.fail(display="World runtime 不可用")
        try:
            obj = world_runtime.get_object(object_id)
            if obj is None:
                return ToolResult.fail(display=f"对象 {object_id} 不存在")
            _log("get_world_object", {"object_id": object_id}, True)
            return ToolResult.ok(
                display=f"id={obj.object_id} state={obj.state}",
                payload={"id": obj.object_id, "state": obj.state, "mutation": False},
            )
        except Exception as e:
            _log("get_world_object", {"object_id": object_id}, False, str(e))
            return ToolResult.fail(display=f"get_world_object 失败: {e}")

    def list_world_links() -> ToolResult:
        """查看 Veritas 世界中的所有对象链接。"""
        if world_runtime is None:
            return ToolResult.fail(display="World runtime 不可用")
        try:
            links = world_runtime.get_links()
            if not links:
                return ToolResult.ok(display="（世界中暂无链接）", payload={"links": [], "mutation": False})
            lines = [f"  {l.from_id} -[{l.link_type}]-> {l.to_id}" for l in links]
            _log("list_world_links", {}, True)
            return ToolResult.ok(
                display="\n".join(lines),
                payload={"links": [{"from": l.from_id, "to": l.to_id, "type": l.link_type} for l in links], "mutation": False},
            )
        except Exception as e:
            _log("list_world_links", {}, False, str(e))
            return ToolResult.fail(display=f"list_world_links 失败: {e}")

    def resolve_path_object(path: str) -> ToolResult:
        """文件路径 → Veritas ObjectId（查 ObjectPathMap）。"""
        try:
            if world_runtime is None:
                return ToolResult.fail(
                    display="resolve_path_object 失败: WorldRuntime 未绑定\n建议: 确认 Runtime 已启动 Veritas。"
                )
            path_n = path.replace("\\\\", "/").lstrip("./")
            oid = None
            path_map = getattr(world_runtime, "_path_map", None)
            if path_map is not None and hasattr(path_map, "find_object_id"):
                oid = path_map.find_object_id(path_n)
                if oid is None:
                    oid = path_map.find_object_id(path)
            if oid is None and hasattr(world_runtime, "find_object_id_for_path"):
                oid = world_runtime.find_object_id_for_path(path_n)
            if oid is None:
                return ToolResult.fail(
                    display=(
                        f"路径 '{path}' 未找到对应 ObjectId\n"
                        f"可能原因: 文件尚未经 create_file 进入 World，或 path map 未重建。\n"
                        f"建议: list_world_objects 或先 create_file。"
                    )
                )
            display = f"RESULT: path={path_n} object_id={int(oid)}"
            return ToolResult.ok(
                display=display,
                payload={"path": path_n, "object_id": int(oid)},
            )
        except Exception as e:
            return ToolResult.fail(display=f"resolve_path_object 失败: {e}")

    return {
        "world_info": world_info,
        "list_world_objects": list_world_objects,
        "get_world_object": get_world_object,
        "list_world_links": list_world_links,
        "resolve_path_object": resolve_path_object,
    }
