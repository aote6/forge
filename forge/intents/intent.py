"""Intent — 一次语义操作的核心数据模型。

Intent 是整个 Forge 的核心抽象。
LLM 产生 Intent，IntentExecutor 消费 Intent。
Intent 不包含 Veritas 原语，只包含语义目标。不包含执行逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class IntentType(Enum):
    CREATE_FILE = "create_file"
    MODIFY_FILE = "modify_file"
    DELETE_FILE = "delete_file"
    CREATE_OBJECT = "create_object"
    LINK_OBJECTS = "link_objects"
    UNLINK_OBJECTS = "unlink_objects"
    FREEZE_OBJECT = "freeze_object"
    DELETE_OBJECT = "delete_object"


@dataclass
class Intent:
    """一次语义操作。

    type: 操作类型
    parameters: 操作参数（语义参数，非 Veritas 原语参数）
    policy: 执行策略
    """
    type: IntentType
    parameters: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create_object(cls, require_confirm: bool = False) -> "Intent":
        """Pure world-object birth. No path, content, or file semantics."""
        return cls(
            type=IntentType.CREATE_OBJECT,
            parameters={},
            policy={"require_confirm": require_confirm},
        )

    @classmethod
    def create_file(
        cls,
        path: str,
        content: str = "",
        overwrite: bool = False,
        require_confirm: bool = True,
    ) -> "Intent":
        return cls(
            type=IntentType.CREATE_FILE,
            parameters={"path": path, "content": content},
            policy={"overwrite": overwrite, "require_confirm": require_confirm},
        )

    @classmethod
    def modify_file(
        cls,
        path: str,
        operations: list[dict],
        require_confirm: bool = True,
    ) -> "Intent":
        return cls(
            type=IntentType.MODIFY_FILE,
            parameters={"path": path, "operations": operations},
            policy={"require_confirm": require_confirm},
        )

    @classmethod
    def delete_file(cls, path: str = "", require_confirm: bool = True) -> "Intent":
        return cls(
            type=IntentType.DELETE_FILE,
            parameters={"path": path},
            policy={"require_confirm": require_confirm},
        )

    @classmethod
    def link_objects(
        cls,
        from_id: int,
        to_id: int,
        link_type: str = "owns",
        require_confirm: bool = False,
    ) -> "Intent":
        return cls(
            type=IntentType.LINK_OBJECTS,
            parameters={"from_id": from_id, "to_id": to_id, "link_type": link_type},
            policy={"require_confirm": require_confirm},
        )

    @classmethod
    def unlink_objects(
        cls,
        from_id: int,
        to_id: int,
        require_confirm: bool = False,
    ) -> "Intent":
        return cls(
            type=IntentType.UNLINK_OBJECTS,
            parameters={"from_id": from_id, "to_id": to_id},
            policy={"require_confirm": require_confirm},
        )

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "parameters": self.parameters,
            "policy": self.policy,
        }
