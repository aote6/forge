"""P10.4: 崩溃恢复端到端测试。

验证场景:
1. 正常恢复: 所有 receipt 消费后 checkpoint 推进
2. 重复启动: 第二次 recovery 不重复消费
3. 中途崩溃: checkpoint 只保存到部分 receipt，重启后只消费未完成的
"""

import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forge.world.types import Receipt, TransactionDelta
from forge.projections.base import ProjectionManager, Projection, ProjectionResult
from forge.recovery.replay import ProjectionRecovery


class TestProjection(Projection):
    """记录每次 apply 的 receipt version。"""

    def __init__(self, name="test"):
        self._name = name
        self.applied_versions: list[int] = []

    @property
    def name(self) -> str:
        return self._name

    def prepare(self, delta):
        return None

    def apply(self, receipt, delta):
        self.applied_versions.append(receipt.version)
        return ProjectionResult(name=self.name, success=True)


class MockWorld:
    """模拟 WorldRuntime，返回可控的历史 receipt 列表。"""

    def __init__(self):
        self._receipts = []

    def set_receipts(self, receipts):
        self._receipts = receipts

    def get_receipts_since(self, since_version):
        return [r for r in self._receipts if r.version > since_version]


def _make_receipt(tx_id: int, version: int) -> Receipt:
    return Receipt(
        tx_id=tx_id, before_root=0, after_root=version * 100,
        version=version, delta=TransactionDelta()
    )


def test_normal_recovery():
    """场景 1: 正常恢复 → checkpoint 推进 → 第二次启动不重复。"""
    ckpt_file = ".forge/projection_checkpoint.json"
    for f in [ckpt_file, ckpt_file + ".tmp"]:
        if os.path.exists(f):
            os.remove(f)

    world = MockWorld()
    world.set_receipts([
        _make_receipt(1, 1),
        _make_receipt(2, 2),
        _make_receipt(3, 3),
    ])

    # 第一次启动
    pm1 = ProjectionManager()
    proj1 = TestProjection("file")
    pm1.register(proj1)

    recovery1 = ProjectionRecovery(world, pm1)
    recovered1 = recovery1.recover()

    assert recovered1 == {"file": 3}, f"expected 3, got {recovered1}"
    assert proj1.applied_versions == [1, 2, 3]
    assert pm1.checkpoint.checkpoints == {"file": 3}

    # 第二次启动：不应再消费
    pm2 = ProjectionManager()
    proj2 = TestProjection("file")
    pm2.register(proj2)

    recovery2 = ProjectionRecovery(world, pm2)
    recovered2 = recovery2.recover()

    assert recovered2 == {"file": 0}, f"expected 0, got {recovered2}"
    assert proj2.applied_versions == [], f"should be empty, got {proj2.applied_versions}"

    os.remove(ckpt_file)
    print("PASS: 正常恢复 + 重复启动不重复消费")


def test_crash_recovery():
    """场景 2: 中途崩溃 — checkpoint 只到 v2，重启后消费 v3。"""
    ckpt_file = ".forge/projection_checkpoint.json"
    for f in [ckpt_file, ckpt_file + ".tmp"]:
        if os.path.exists(f):
            os.remove(f)

    world = MockWorld()
    world.set_receipts([
        _make_receipt(1, 1),
        _make_receipt(2, 2),
        _make_receipt(3, 3),
    ])

    # 模拟: v1 和 v2 成功，但 v3 的 checkpoint 写入失败（手动删除 checkpoint 模拟）
    pm1 = ProjectionManager()
    proj1 = TestProjection("file")
    pm1.register(proj1)

    recovery1 = ProjectionRecovery(world, pm1)
    recovery1.recover()

    # 此时 checkpoint=3
    assert pm1.checkpoint.checkpoints == {"file": 3}
    assert proj1.applied_versions == [1, 2, 3]

    # 模拟崩溃：把 checkpoint 回退到 v2
    pm1.checkpoint._checkpoints["file"] = 2
    pm1.checkpoint._save()

    # "重启"：checkpoint 从文件加载
    pm2 = ProjectionManager()
    proj2 = TestProjection("file")
    pm2.register(proj2)

    recovery2 = ProjectionRecovery(world, pm2)
    recovered2 = recovery2.recover()

    # 应该只恢复 v3
    assert recovered2 == {"file": 1}, f"expected 1, got {recovered2}"
    assert proj2.applied_versions == [3], f"expected [3], got {proj2.applied_versions}"
    assert pm2.checkpoint.checkpoints == {"file": 3}

    os.remove(ckpt_file)
    print("PASS: 崩溃恢复 — 只重放未完成的事务")


if __name__ == "__main__":
    test_normal_recovery()
    test_crash_recovery()
    print("\nP10.4 崩溃恢复测试全部通过")
