"""IntentExecutor — 把 Intent 展开成 Veritas 原语序列并执行。

IntentExecutor 是事务编排器（Transaction Orchestrator）。
它知道如何把语义操作翻译成 world_begin/create_object/write/link/commit 序列。
它不知道文件系统、Git、Projection。
"""

from __future__ import annotations

from typing import Optional

from forge.intents.intent import Intent, IntentType
from forge.world.runtime import WorldRuntime
from forge.world.types import Receipt, TransactionDelta


class IntentExecutionError(Exception):
    pass


class IntentExecutor:
    """把 Intent 编排成一次完整事务。"""

    def __init__(self, world: WorldRuntime):
        self._world = world

    def execute(self, intent: Intent) -> tuple[Receipt, TransactionDelta]:
        """执行一个 Intent，返回 (Receipt, TransactionDelta)。

        对于需要用户确认的操作（policy.require_confirm=True），
        调用方应先调 execute_dry_run() 获取预览，再调 execute() 提交。
        """
        handler = self._handlers.get(intent.type)
        if handler is None:
            raise IntentExecutionError(f"unknown intent type: {intent.type}")
        return handler(intent)

    def execute_dry_run(self, intent: Intent) -> dict:
        """试运行 Intent 的 prepare 阶段，返回确认信息（如 diff）。"""
        handler = self._dry_run_handlers.get(intent.type)
        if handler is None:
            return {}
        return handler(intent)

    # ── handlers ────────────────────────────────────────────

    def _handle_create_file(self, intent: Intent) -> tuple[Receipt, TransactionDelta]:
        path = intent.parameters["path"]
        content = intent.parameters.get("content", "")

        session = self._world.begin_session()
        obj_id = session.create_object()
        session.write(obj_id, 0, value=path)     # state_id=0: 文件路径
        session.write(obj_id, 1, value=content)  # state_id=1: 文件内容

        return self._world.commit_session()

    def _handle_modify_file(self, intent: Intent) -> tuple[Receipt, TransactionDelta]:
        path = intent.parameters["path"]
        operations = intent.parameters["operations"]
        object_id = intent.parameters.get("object_id")

        if object_id is None:
            raise IntentExecutionError("modify_file requires object_id")

        session = self._world.begin_session()
        # 把操作序列化为 JSON 写入 object
        import json
        session.write(object_id, 2, value=json.dumps(operations))
        return self._world.commit_session()

    def _handle_delete_file(self, intent: Intent) -> tuple[Receipt, TransactionDelta]:
        object_id = intent.parameters.get("object_id")
        if object_id is None:
            raise IntentExecutionError("delete_file requires object_id")

        session = self._world.begin_session()
        session.death(object_id)
        return self._world.commit_session()

    def _handle_link_objects(self, intent: Intent) -> tuple[Receipt, TransactionDelta]:
        from_id = intent.parameters["from_id"]
        to_id = intent.parameters["to_id"]
        link_type = intent.parameters.get("link_type", "owns")

        session = self._world.begin_session()
        session.link(from_id, to_id, link_type)
        return self._world.commit_session()

    def _handle_unlink_objects(self, intent: Intent) -> tuple[Receipt, TransactionDelta]:
        from_id = intent.parameters["from_id"]
        to_id = intent.parameters["to_id"]

        session = self._world.begin_session()
        session.unlink(from_id, to_id)
        return self._world.commit_session()

    def _handle_freeze_object(self, intent: Intent) -> tuple[Receipt, TransactionDelta]:
        object_id = intent.parameters["object_id"]

        session = self._world.begin_session()
        session.freeze(object_id)
        return self._world.commit_session()

    # ── dry run handlers ────────────────────────────────────

    def _dry_run_create_file(self, intent: Intent) -> dict:
        return {
            "type": "create_file",
            "path": intent.parameters["path"],
            "content_preview": intent.parameters.get("content", "")[:200],
        }

    def _dry_run_modify_file(self, intent: Intent) -> dict:
        return self._world.prepare_commit()

    @property
    def _handlers(self) -> dict:
        return {
            IntentType.CREATE_FILE: self._handle_create_file,
            IntentType.MODIFY_FILE: self._handle_modify_file,
            IntentType.DELETE_FILE: self._handle_delete_file,
            IntentType.LINK_OBJECTS: self._handle_link_objects,
            IntentType.UNLINK_OBJECTS: self._handle_unlink_objects,
            IntentType.FREEZE_OBJECT: self._handle_freeze_object,
        }

    @property
    def _dry_run_handlers(self) -> dict:
        return {
            IntentType.CREATE_FILE: self._dry_run_create_file,
            IntentType.MODIFY_FILE: self._dry_run_modify_file,
        }
