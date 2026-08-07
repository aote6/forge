"""IntentExecutor — 把 Intent 展开成 Veritas 原语序列并执行。

IntentExecutor 是事务编排器（Transaction Orchestrator）。
它知道如何把语义操作翻译成 world begin/create_object/write/link/commit 序列。
它不知道文件系统、Git、Projection。
"""

from __future__ import annotations

import json
from typing import Optional

from forge.intents.intent import Intent, IntentType
from forge.world.runtime import WorldRuntime
from forge.world.types import Receipt, TransactionDelta


class IntentExecutionError(Exception):
    pass


class IntentExecutor:
    """把 Intent 编排成一次完整事务或准备阶段。"""

    def __init__(self, world: WorldRuntime):
        self._world = world

    def execute(self, intent: Intent) -> tuple[Receipt, TransactionDelta]:
        """执行并提交 Intent，返回 (Receipt, TransactionDelta)。不触发 Projection。"""
        handler = self._handlers.get(intent.type)
        if handler is None:
            raise IntentExecutionError(f"unknown intent type: {intent.type}")
        return handler(intent)

    def execute_batch(self, intents: list[Intent]) -> tuple[Receipt, TransactionDelta]:
        """Execute multiple intents in a single Veritas transaction.

        All operations share one begin_session/commit_session pair.
        If any intent fails, the entire transaction is aborted.
        """
        if not intents:
            raise IntentExecutionError("execute_batch requires at least one intent")
        session = self._world.begin_session()
        try:
            for intent in intents:
                handler = self._handlers.get(intent.type)
                if handler is None:
                    raise IntentExecutionError(f"unknown intent type: {intent.type}")
                handler(intent)
        except Exception:
            self._world._current_session = session
            session.abort()
            raise
        return self._world.commit_session()

    def stage(self, intent: Intent) -> TransactionDelta:
        """在当前或新 session 中暂存 Intent，不提交。用于 require_confirm。"""
        handler = self._stage_handlers.get(intent.type)
        if handler is None:
            raise IntentExecutionError(f"cannot stage intent type: {intent.type}")
        return handler(intent)

    def execute_dry_run(self, intent: Intent) -> dict:
        """试运行预览（不修改世界）。"""
        handler = self._dry_run_handlers.get(intent.type)
        if handler is None:
            return {}
        return handler(intent)

    # ── full execute handlers ────────────────────────────────

    def _handle_create_file(self, intent: Intent) -> tuple[Receipt, TransactionDelta]:
        path = intent.parameters["path"]
        content = intent.parameters.get("content", "")
        session = self._world.begin_session()
        obj_id = session.create_object()
        session.write(obj_id, 0, value=path)
        session.write(obj_id, 1, value=content)
        return self._world.commit_session()

    def _handle_modify_file(self, intent: Intent) -> tuple[Receipt, TransactionDelta]:
        path = intent.parameters["path"]
        operations = intent.parameters["operations"]
        object_id = intent.parameters.get("object_id")
        if object_id is None:
            raise IntentExecutionError("modify_file requires object_id")
        session = self._world.begin_session()
        session.write(object_id, 0, value=path)
        session.write(object_id, 2, value=json.dumps(operations))
        return self._world.commit_session()

    def _handle_delete_file(self, intent: Intent) -> tuple[Receipt, TransactionDelta]:
        object_id = intent.parameters.get("object_id")
        path = intent.parameters.get("path", "")
        if object_id is None:
            raise IntentExecutionError("delete_file requires object_id")
        session = self._world.begin_session()
        if path:
            session.write(object_id, 0, value=path)
        session.death(object_id)
        receipt, delta = self._world.commit_session()
        if path:
            delta.metadata = dict(delta.metadata or {})
            delta.metadata.setdefault("deleted_paths", {})[object_id] = path
        return receipt, delta

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

    # ── stage handlers (no commit) ───────────────────────────

    def _stage_create_file(self, intent: Intent) -> TransactionDelta:
        path = intent.parameters["path"]
        content = intent.parameters.get("content", "")
        session = self._world.begin_session()
        obj_id = session.create_object()
        session.write(obj_id, 0, value=path)
        session.write(obj_id, 1, value=content)
        return session.preview_delta()

    def _stage_modify_file(self, intent: Intent) -> TransactionDelta:
        path = intent.parameters["path"]
        operations = intent.parameters["operations"]
        object_id = intent.parameters.get("object_id")
        if object_id is None:
            raise IntentExecutionError("modify_file requires object_id")
        session = self._world.begin_session()
        session.write(object_id, 0, value=path)
        session.write(object_id, 2, value=json.dumps(operations))
        return session.preview_delta()

    def _stage_delete_file(self, intent: Intent) -> TransactionDelta:
        object_id = intent.parameters.get("object_id")
        path = intent.parameters.get("path", "")
        if object_id is None:
            raise IntentExecutionError("delete_file requires object_id")
        session = self._world.begin_session()
        if path:
            session.write(object_id, 0, value=path)
        session.death(object_id)
        delta = session.preview_delta()
        if path:
            delta.metadata = dict(delta.metadata or {})
            delta.metadata.setdefault("deleted_paths", {})[object_id] = path
        return delta

    # ── dry run ──────────────────────────────────────────────

    def _dry_run_create_file(self, intent: Intent) -> dict:
        return {
            "type": "create_file",
            "path": intent.parameters["path"],
            "content_preview": intent.parameters.get("content", "")[:200],
        }

    def _dry_run_modify_file(self, intent: Intent) -> dict:
        return {
            "type": "modify_file",
            "path": intent.parameters.get("path"),
            "operations": intent.parameters.get("operations"),
        }

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
    def _stage_handlers(self) -> dict:
        return {
            IntentType.CREATE_FILE: self._stage_create_file,
            IntentType.MODIFY_FILE: self._stage_modify_file,
            IntentType.DELETE_FILE: self._stage_delete_file,
        }

    @property
    def _dry_run_handlers(self) -> dict:
        return {
            IntentType.CREATE_FILE: self._dry_run_create_file,
            IntentType.MODIFY_FILE: self._dry_run_modify_file,
        }
