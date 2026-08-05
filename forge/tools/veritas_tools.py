"""
World tools for the LLM.

All world interaction goes through WorldRuntime.
VeritasClient is not used here.
"""

from __future__ import annotations

from forge.adapters.base import ToolResult


def make_world_tools(world_runtime):
    """Build tool callables bound to a WorldRuntime instance."""

    def world_whoami() -> ToolResult:
        try:
            oid = world_runtime.whoami()
            if oid is None:
                oid = world_runtime.ensure_identity()
            return ToolResult.ok(
                display=f"Forge world identity ObjectId={oid}",
                payload={"object_id": oid},
            )
        except Exception as e:
            return ToolResult.fail(display=f"whoami failed: {e}")

    def world_info() -> ToolResult:
        try:
            info = world_runtime.world_info()
            return ToolResult.ok(
                display=(
                    f"version={info.version} state_root={info.state_root} "
                    f"objects={info.object_count}"
                ),
                payload={
                    "version": info.version,
                    "state_root": info.state_root,
                    "object_count": info.object_count,
                },
            )
        except Exception as e:
            return ToolResult.fail(display=f"world_info failed: {e}")

    def world_list_objects() -> ToolResult:
        try:
            objs = world_runtime.list_objects()
            if not objs:
                return ToolResult.ok(display="（世界为空，无 Object）", payload={"objects": []})
            lines = [f"  {o.object_id:<8} {o.state}" for o in objs]
            return ToolResult.ok(
                display="Object ID  State\n" + "\n".join(lines),
                payload={"objects": [{"id": o.object_id, "state": o.state} for o in objs]},
            )
        except Exception as e:
            return ToolResult.fail(display=f"list_objects failed: {e}")

    def world_get_object(object_id: int) -> ToolResult:
        try:
            info = world_runtime.get_object(int(object_id))
            if info is None:
                return ToolResult.fail(display=f"Object {object_id} 不存在")
            return ToolResult.ok(
                display=f"Object {info.object_id}: {info.state}",
                payload={"object_id": info.object_id, "state": info.state},
            )
        except Exception as e:
            return ToolResult.fail(display=f"get_object failed: {e}")

    def world_get_links() -> ToolResult:
        try:
            links = world_runtime.get_links()
            if not links:
                return ToolResult.ok(display="（无 Link）", payload={"links": []})
            lines = [f"  {l.from_id} -> {l.to_id} [{l.link_type}]" for l in links]
            return ToolResult.ok(
                display="Links\n" + "\n".join(lines),
                payload={
                    "links": [
                        {"from": l.from_id, "to": l.to_id, "link_type": l.link_type}
                        for l in links
                    ]
                },
            )
        except Exception as e:
            return ToolResult.fail(display=f"get_links failed: {e}")

    def world_begin() -> ToolResult:
        try:
            session = world_runtime.begin_session()
            return ToolResult.ok(
                display=f"Session {session.session_id} begun (actor={session.actor_id})",
                payload={"session_id": session.session_id, "actor_id": session.actor_id},
            )
        except Exception as e:
            return ToolResult.fail(display=f"begin_session failed: {e}")

    def _session():
        s = world_runtime.current_session
        if s is None:
            raise RuntimeError("no active world session; call world_begin first")
        return s

    def world_create_object() -> ToolResult:
        try:
            oid = _session().create_object()
            return ToolResult.ok(
                display=f"Object {oid} staged in session (Alive after commit)",
                payload={"object_id": oid},
            )
        except Exception as e:
            return ToolResult.fail(display=f"create_object failed: {e}")

    def world_freeze(object_id: int) -> ToolResult:
        try:
            _session().freeze(int(object_id))
            return ToolResult.ok(display=f"Object {object_id} freeze staged")
        except Exception as e:
            return ToolResult.fail(display=f"freeze failed: {e}")

    def world_death(object_id: int) -> ToolResult:
        try:
            _session().death(int(object_id))
            return ToolResult.ok(display=f"Object {object_id} death staged")
        except Exception as e:
            return ToolResult.fail(display=f"death failed: {e}")

    def world_link(from_id: int, to_id: int, link_type: str = "owns") -> ToolResult:
        try:
            _session().link(int(from_id), int(to_id), link_type)
            return ToolResult.ok(
                display=f"Link {from_id} -[{link_type}]-> {to_id} staged"
            )
        except Exception as e:
            return ToolResult.fail(display=f"link failed: {e}")

    def world_unlink(from_id: int, to_id: int) -> ToolResult:
        try:
            _session().unlink(int(from_id), int(to_id))
            return ToolResult.ok(display=f"Unlink {from_id} -> {to_id} staged")
        except Exception as e:
            return ToolResult.fail(display=f"unlink failed: {e}")

    def world_commit() -> ToolResult:
        try:
            receipt = _session().commit()
            return ToolResult.ok(
                display=(
                    f"Committed tx={receipt.tx_id} version={receipt.version}\n"
                    f"  before_root={receipt.before_root}\n"
                    f"  after_root={receipt.after_root}"
                ),
                payload={
                    "tx_id": receipt.tx_id,
                    "before_root": receipt.before_root,
                    "after_root": receipt.after_root,
                    "version": receipt.version,
                },
            )
        except Exception as e:
            return ToolResult.fail(display=f"commit failed: {e}")

    def world_abort() -> ToolResult:
        try:
            s = world_runtime.current_session
            if s is None:
                return ToolResult.ok(display="no active session")
            s.abort()
            return ToolResult.ok(display="session aborted")
        except Exception as e:
            return ToolResult.fail(display=f"abort failed: {e}")

    # Deprecated aliases (route through WorldRuntime observation / session)
    def veritas_list_objects() -> ToolResult:
        return world_list_objects()

    def veritas_get_object(object_id: int) -> ToolResult:
        return world_get_object(object_id)

    def veritas_create_object() -> ToolResult:
        """Deprecated: prefers session path; auto-begins if needed."""
        try:
            if world_runtime.current_session is None:
                world_runtime.begin_session()
            return world_create_object()
        except Exception as e:
            return ToolResult.fail(display=f"create failed: {e}")

    def veritas_object_exists(object_id: int) -> ToolResult:
        try:
            info = world_runtime.get_object(int(object_id))
            exists = info is not None
            return ToolResult.ok(
                display=f"Object {object_id} exists={exists}",
                payload={"exists": exists, "state": None if info is None else info.state},
            )
        except Exception as e:
            return ToolResult.fail(display=f"exists check failed: {e}")

    return {
        "world_whoami": world_whoami,
        "world_info": world_info,
        "world_list_objects": world_list_objects,
        "world_get_object": world_get_object,
        "world_get_links": world_get_links,
        "world_begin": world_begin,
        "world_create_object": world_create_object,
        "world_freeze": world_freeze,
        "world_death": world_death,
        "world_link": world_link,
        "world_unlink": world_unlink,
        "world_commit": world_commit,
        "world_abort": world_abort,
        # deprecated
        "veritas_list_objects": veritas_list_objects,
        "veritas_get_object": veritas_get_object,
        "veritas_create_object": veritas_create_object,
        "veritas_object_exists": veritas_object_exists,
    }


# Back-compat name
def make_veritas_tools(world_runtime):
    return make_world_tools(world_runtime)
