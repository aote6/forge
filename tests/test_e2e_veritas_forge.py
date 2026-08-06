"""端到端测试：veritasd → Forge Receipt → FileProjection → 文件落地 + 幂等。

需要 veritasd 二进制在 ../veritas_kernel/target/debug/veritasd
"""

import subprocess, os, json, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_e2e():
    veritasd_bin = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "veritas_kernel", "target", "debug", "veritasd"
    )
    if not os.path.exists(veritasd_bin):
        print(f"SKIP: veritasd not found at {veritasd_bin}")
        return

    proc = subprocess.Popen(
        [veritasd_bin], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True
    )

    def send(cmd):
        proc.stdin.write(json.dumps(cmd) + "\n"); proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    try:
        # 1-3: 创建事务
        identity = send({"cmd": "attach_identity"})["object_id"]
        sid = send({"cmd": "tx_begin", "actor_id": identity})["session_id"]
        oid = send({"cmd": "tx_create_object", "session_id": sid})["object_id"]

        # 4-5: 写入路径和内容
        test_path = os.path.expanduser("~/forge_e2e_test.py")
        send({"cmd": "tx_write", "session_id": sid, "state_id": 0, "hex": test_path.encode().hex()})
        send({"cmd": "tx_write", "session_id": sid, "state_id": 1, "hex": "print('E2E')\n".encode().hex()})

        # 6: commit
        resp = send({"cmd": "tx_commit", "session_id": sid})

        # 7-8: Forge 解析
        from forge.world.receipt_parser import parse_receipt
        receipt = parse_receipt(resp)
        assert receipt.tx_id > 0
        assert receipt.delta.actor_id == identity

        # 9: ObjectPathMap
        from forge.projections.object_path import ObjectPathMap
        pmap = ObjectPathMap()
        pmap.update_from_delta(receipt.delta)
        assert test_path in pmap._paths.values()

        # 10: FileProjection.apply
        from forge.projections.file_projection import FileProjection
        fp = FileProjection(project_root=".", object_path_map=pmap)
        result = fp.apply(receipt, receipt.delta)
        assert result.success
        assert os.path.exists(test_path)
        with open(test_path) as f:
            assert f.read() == "print('E2E')\n"
        os.remove(test_path)

        # 11-12: 幂等
        from forge.projections.base import ProjectionManager
        pm = ProjectionManager()
        pm.register(fp)
        r1 = pm.project(receipt, receipt.delta)
        r2 = pm.project(receipt, receipt.delta)
        assert any("skipped" in r.reason for r in r2)

    finally:
        proc.stdin.close(); proc.terminate(); proc.wait()

    print("E2E test passed: 12/12 steps")


if __name__ == "__main__":
    test_e2e()
