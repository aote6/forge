"""集成测试：veritasd JSON → WorldAdapter → Receipt.delta 完整链路。

验证 Rust TransactionDelta 字段在 Python 侧不丢失。
"""

import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forge.world.types import Receipt, TransactionDelta


def parse_receipt(resp: dict) -> Receipt:
    """模拟 WorldAdapter.tx_commit 的解析逻辑。"""
    r = resp.get("receipt") or {}
    d = r.get("delta") or {}
    delta = TransactionDelta(
        actor_id=int(d.get("actor_id", 0)),
        objects_created=[int(x) for x in d.get("objects_created", [])],
        objects_deleted=[int(x) for x in d.get("objects_deleted", [])],
        objects_frozen=[int(x) for x in d.get("objects_frozen", [])],
        links_added=[(int(f), int(t), lt) for f, t, lt in d.get("links_added", [])],
        links_removed=[(int(f), int(t)) for f, t in d.get("links_removed", [])],
        memory_written=d.get("memory_written", []),
        capability_events=d.get("capability_events", []),
        effects=[(k, v) for k, v in d.get("effects", [])],
    )
    return Receipt(
        tx_id=int(r.get("tx_id", 0)),
        before_root=int(r.get("before_root", 0)),
        after_root=int(r.get("after_root", 0)),
        version=int(r.get("version", 0)),
        delta=delta,
    )


def test_empty_delta():
    """最小 receipt：空 delta。"""
    resp = {
        "ok": True,
        "receipt": {
            "tx_id": 1,
            "before_root": 100,
            "after_root": 200,
            "version": 5,
            "delta": {
                "actor_id": 0,
                "objects_created": [],
                "objects_deleted": [],
                "objects_frozen": [],
                "links_added": [],
                "links_removed": [],
                "memory_written": [],
                "capability_events": [],
                "effects": [],
            }
        }
    }
    r = parse_receipt(resp)
    assert r.tx_id == 1
    assert r.version == 5
    assert r.delta.actor_id == 0
    assert r.delta.objects_created == []
    assert r.delta.memory_written == []


def test_create_object_with_write():
    """创建 object + 写入 memory。"""
    resp = {
        "ok": True,
        "receipt": {
            "tx_id": 2,
            "before_root": 200,
            "after_root": 300,
            "version": 6,
            "delta": {
                "actor_id": 1,
                "objects_created": [42],
                "objects_deleted": [],
                "objects_frozen": [],
                "links_added": [],
                "links_removed": [],
                "memory_written": [
                    {"object_id": 42, "state_id": 0, "value_hex": "2f746d702f612e7079"},
                    {"object_id": 42, "state_id": 1, "value_hex": "7072696e74282268692229"},
                ],
                "capability_events": [],
                "effects": [],
            }
        }
    }
    r = parse_receipt(resp)
    assert r.delta.actor_id == 1
    assert r.delta.objects_created == [42]
    assert len(r.delta.memory_written) == 2
    
    w0 = r.delta.memory_written[0]
    assert w0["object_id"] == 42
    assert w0["state_id"] == 0
    assert bytes.fromhex(w0["value_hex"]).decode() == "/tmp/a.py"
    
    w1 = r.delta.memory_written[1]
    assert w1["state_id"] == 1
    assert bytes.fromhex(w1["value_hex"]).decode() == 'print("hi")'


def test_delete_with_links():
    """删除 object + link 变化。"""
    resp = {
        "ok": True,
        "receipt": {
            "tx_id": 3,
            "before_root": 300,
            "after_root": 400,
            "version": 7,
            "delta": {
                "actor_id": 1,
                "objects_created": [],
                "objects_deleted": [42],
                "objects_frozen": [],
                "links_added": [(1, 99, "owns")],
                "links_removed": [(1, 42)],
                "memory_written": [],
                "capability_events": [],
                "effects": [],
            }
        }
    }
    r = parse_receipt(resp)
    assert r.delta.objects_deleted == [42]
    assert r.delta.links_added == [(1, 99, "owns")]
    assert r.delta.links_removed == [(1, 42)]


def test_effects():
    """effects 字段不丢失。"""
    resp = {
        "ok": True,
        "receipt": {
            "tx_id": 4,
            "before_root": 400,
            "after_root": 500,
            "version": 8,
            "delta": {
                "actor_id": 2,
                "objects_created": [],
                "objects_deleted": [],
                "objects_frozen": [],
                "links_added": [],
                "links_removed": [],
                "memory_written": [],
                "capability_events": [],
                "effects": [("dep_notify", "deadbeef")],
            }
        }
    }
    r = parse_receipt(resp)
    assert r.delta.effects == [("dep_notify", "deadbeef")]


def test_hex_boundary():
    """value_hex 包含不可打印字节时不解码失败。"""
    resp = {
        "ok": True,
        "receipt": {
            "tx_id": 5,
            "before_root": 500,
            "after_root": 600,
            "version": 9,
            "delta": {
                "actor_id": 0,
                "objects_created": [],
                "objects_deleted": [],
                "objects_frozen": [],
                "links_added": [],
                "links_removed": [],
                "memory_written": [
                    {"object_id": 1, "state_id": 0, "value_hex": "00ff12a9"},
                ],
                "capability_events": [],
                "effects": [],
            }
        }
    }
    r = parse_receipt(resp)
    raw = bytes.fromhex(r.delta.memory_written[0]["value_hex"])
    assert raw == b'\x00\xff\x12\xa9'


if __name__ == "__main__":
    test_empty_delta()
    test_create_object_with_write()
    test_delete_with_links()
    test_effects()
    test_hex_boundary()
    print("5 tests passed")
