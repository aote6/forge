"""Veritas Adapter — 统一事务接口

Forge 不直接操作 Veritas 内部，全部通过此 adapter。
"""
from forge.world.runtime import WorldRuntime
from forge.protocols.models import TransactionRequest, TransactionReceipt


class VeritasAdapter:
    """Veritas 事务适配器"""

    def __init__(self, project_root: str):
        self.world = WorldRuntime(project_root=project_root)
        self.world.ensure_identity()

    def execute(self, request: TransactionRequest) -> TransactionReceipt:
        """执行事务，返回回执"""
        try:
            session = self.world.begin_session()
            for file_op in request.files:
                path = file_op["path"]
                content = file_op["content"]
                obj_id = session.create_object()
                session.write(obj_id, 0, value=path)
                session.write(obj_id, 1, value=content)

            receipt, delta = self.world.commit_session()

            return TransactionReceipt(
                tx_id=receipt.tx_id,
                version=receipt.version,
                success=True
            )
        except Exception as e:
            return TransactionReceipt(
                tx_id=0, version=0, success=False, error=str(e)
            )

    def close(self):
        self.world.close()
