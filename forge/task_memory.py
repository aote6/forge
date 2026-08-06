"""TaskMemory — 任务进度持久化

独立于 Veritas WAL，只存 Runtime 侧的任务推理状态。
删除后可从 Veritas 重建（重新 Plan），符合 RECOVERY_CONSTITUTION 附录第5条。
"""
import json
import os
import sys
from datetime import datetime
from typing import Optional

from forge.contracts.execution import TaskCheckpoint
from forge.contracts.planning import Plan
from forge.contracts.constitution import ChangeProposal, ConstitutionResult, CheckStatus
from forge.contracts.verification import VerificationResult

TASK_MEMORY_DIR = ".forge/tasks"


class TaskMemory:
    """管理任务进度的持久化存储"""

    def __init__(self, project_root: str):
        self._dir = os.path.join(project_root, TASK_MEMORY_DIR)
        os.makedirs(self._dir, exist_ok=True)

    def save(self, checkpoint: TaskCheckpoint) -> str:
        """保存 checkpoint 到文件，返回文件路径"""
        path = os.path.join(self._dir, f"{checkpoint.task_id}.json")
        data = {
            "task_id": checkpoint.task_id,
            "phase": checkpoint.phase,
            "plan_id": checkpoint.plan_id,
            "completed_steps": checkpoint.completed_steps,
            "state": checkpoint.state,
            "updated": datetime.now().isoformat()
        }
        # 原子写入
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        print(f"[TaskMemory] 保存 checkpoint: {checkpoint.task_id} phase={checkpoint.phase}", file=sys.stderr)
        return path

    def load(self, task_id: str) -> Optional[TaskCheckpoint]:
        """加载已保存的 checkpoint"""
        path = os.path.join(self._dir, f"{task_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[TaskMemory] 恢复 checkpoint: {task_id} phase={data.get('phase')}", file=sys.stderr)
        return TaskCheckpoint(
            task_id=data["task_id"],
            phase=data["phase"],
            plan_id=data.get("plan_id"),
            completed_steps=data.get("completed_steps", []),
            state=data.get("state", {})
        )

    def list_tasks(self) -> list[dict]:
        """列出所有已保存的任务"""
        tasks = []
        if not os.path.isdir(self._dir):
            return tasks
        for fname in sorted(os.listdir(self._dir)):
            if fname.endswith(".json"):
                path = os.path.join(self._dir, fname)
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                tasks.append({
                    "task_id": data["task_id"],
                    "phase": data["phase"],
                    "updated": data.get("updated", ""),
                    "plan_id": data.get("plan_id", "")
                })
        return tasks

    def delete(self, task_id: str) -> bool:
        """删除任务 checkpoint"""
        path = os.path.join(self._dir, f"{task_id}.json")
        if os.path.exists(path):
            os.remove(path)
            return True
        return False


# ─── TaskCheckpoint 工厂函数 ───

def make_checkpoint(
    task_id: str,
    phase: str,
    plan: Optional[Plan] = None,
    completed_steps: Optional[list] = None,
    extra_state: Optional[dict] = None
) -> TaskCheckpoint:
    """创建 TaskCheckpoint 的便捷工厂"""
    return TaskCheckpoint(
        task_id=task_id,
        phase=phase,
        plan_id=plan.plan_id if plan else None,
        completed_steps=completed_steps or [],
        state={
                **(extra_state or {}),
                "plan_goal": plan.goal if plan else "",
                "total_steps": len(plan.steps) if plan else 0,
            }
    )
