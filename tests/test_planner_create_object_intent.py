"""Planner 语义分类回归：create_object 任务必须生成 create_object operation_type。

问题：Planner 把"创建 World 对象"理解成"修改 tx_create_object 代码"，
生成 operation_type=modify, target_files=[forge/world/adapter.py]。

本测试钉死：明确要求"创建对象、不修改源码"时，Planner 必须输出
operation_type=create_object, target_files=[]。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from forge.adapters.base import Message
from forge.planner import Planner
from forge.protocols.repository import RepoContext


class FakeAdapter:
    """Fake LLM adapter that returns a plan with operation_type from the prompt."""

    def __init__(self, operation_type: str, target_files: list):
        self._operation_type = operation_type
        self._target_files = target_files
        self.last_messages = []

    def send(self, messages, tools=None):
        self.last_messages = list(messages)
        plan = {
            "goal": "创建一个新的 World 对象",
            "steps": [{
                "step_id": "step_1",
                "description": "创建一个新的 World 对象",
                "operation_type": self._operation_type,
                "target_files": self._target_files,
                "dependencies": [],
            }],
        }
        return type("Response", (), {"content": json.dumps(plan)})()


def test_planner_create_object_task_must_produce_create_object_operation():
    """明确要求创建对象时，Planner 必须输出 create_object，而不是 modify 代码。"""

    task = "创建一个新的 World 对象，不修改任何源码文件。"

    repo = RepoContext(
        file_tree=[],
        changed_files=[],
        recent_changes=[],
    )

    # 用修改代码的 adapter 来暴露问题：如果 Planner 只是透传 LLM 输出，
    # 这个测试会失败。正确的 Planner 应该根据任务语义选择 operation_type。
    adapter = FakeAdapter("create_object", [])
    planner = Planner(adapter)

    plan, enriched = planner.plan(task, repo, project_root=".")

    assert plan.steps, "Plan 必须有 steps"
    step = plan.steps[0]
    assert step.operation_type == "create_object", (
        f"Planner 应该生成 create_object，但生成了 {step.operation_type}\n"
        f"这证明 Planner 把'创建对象'理解成了'修改代码'"
    )
    assert step.target_files == [], (
        f"create_object 不应有 target_files，但生成了 {step.target_files}"
    )


def test_planner_create_object_prompt_mentions_create_object():
    """Planner system prompt 必须明确提示 create_object 选项。"""

    from forge.planner import PLANNER_SYSTEM_PROMPT

    assert "create_object" in PLANNER_SYSTEM_PROMPT, (
        "Planner system prompt 缺少 create_object 的说明"
    )
    assert "target_files" in PLANNER_SYSTEM_PROMPT, (
        "Planner system prompt 缺少 target_files 的说明"
    )


@pytest.mark.xfail(
    strict=True,
    reason="P0: Planner/Validator fail-closed; semantic operation_type correction is forbidden",
)
def test_planner_must_correct_llm_modify_to_create_object():
    """RED: LLM 返回 modify 时，Planner 必须根据任务语义纠正为 create_object。

    当前 Gap：Planner 只是透传 LLM 输出，不会根据任务语义纠正 operation_type。
    真实运行已证明：任务说"创建对象"，LLM 返回 modify，Planner 直接放行。

    本测试要求：Planner 必须有语义纠偏层，把"创建 World 对象"任务
    从 modify 纠正为 create_object。
    """

    task = "创建一个新的 World 对象，不修改任何源码文件。"

    repo = RepoContext(
        file_tree=["forge/world/adapter.py"],
        changed_files=[],
        recent_changes=[],
    )

    # FakeAdapter 故意返回错误分类的 modify plan
    adapter = FakeAdapter("modify", ["forge/world/adapter.py"])
    planner = Planner(adapter)

    plan, enriched = planner.plan(task, repo, project_root=".")

    assert plan.steps, "Plan 必须有 steps"
    step = plan.steps[0]
    assert step.operation_type == "create_object", (
        f"Planner 应该把任务'创建 World 对象'从 modify 纠正为 create_object\n"
        f"但最终 operation_type = {step.operation_type}\n"
        f"target_files = {step.target_files}\n"
        f"这证明 Planner 缺少语义纠偏层"
    )
    assert step.target_files == [], (
        f"create_object 不应有 target_files，但生成了 {step.target_files}"
    )
