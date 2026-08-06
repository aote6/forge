"""集成测试：veritasd JSON → parse_receipt → Receipt.delta 完整链路。

验证 Rust TransactionDelta 字段在 Python 侧不丢失。
使用生产代码 forge.world.receipt_parser.parse_receipt。
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forge.world.receipt_parser import parse_receipt


def test_empty_delta():
    resp = {
        "ok": True,
        "receipt": {
            "tx_id": 1, "before_root": 100, "after_root": 200, "version": 5,
            "delta": {"actor_id": 0, "objects_created": [], "objects_deleted": [],
                      "objects_frozen": [], "links_added": [], "links_removed": [],
                      "memory_written": [], "capability_events": [], "effects": []}
        }
    }
    r = parse_receipt(resp)
    assert r.tx_id == 1
    assert r.delta.actor_id == 0
    assert r.delta.memory_written == []


def test_create_object_with_write():
    resp = {
        "ok": True,
        "receipt": {
            "tx_id": 2, "before_root": 200, "after_root": 300, "version": 6,
            "delta": {
                "actor_id": 1,
                "objects_created": [42],
                "objects_deleted": [], "objects_frozen": [],
                "links_added": [], "links_removed": [],
                "memory_written": [
                    {"object_id": 42, "state_id": 0, "value_hex": "2f746d702f612e7079"},
                    {"object_id": 42, "state_id": 1, "value_hex": "7072696e74282268692229"},
                ],
                "capability_events": [], "effects": [],
            }
        }
    }
    r = parse_receipt(resp)
    assert r.delta.actor_id == 1
    assert r.delta.objects_created == [42]
    assert len(r.delta.memory_written) == 2
    assert bytes.fromhex(r.delta.memory_written[0]["value_hex"]).decode() == "/tmp/a.py"
    assert bytes.fromhex(r.delta.memory_written[1]["value_hex"]).decode() == 'print("hi")'


def test_delete_with_links():
    resp = {
        "ok": True,
        "receipt": {
            "tx_id": 3, "before_root": 300, "after_root": 400, "version": 7,
            "delta": {
                "actor_id": 1,
                "objects_created": [], "objects_deleted": [42], "objects_frozen": [],
                "links_added": [(1, 99, "owns")], "links_removed": [(1, 42)],
                "memory_written": [], "capability_events": [], "effects": [],
            }
        }
    }
    r = parse_receipt(resp)
    assert r.delta.objects_deleted == [42]
    assert r.delta.links_added == [(1, 99, "owns")]
    assert r.delta.links_removed == [(1, 42)]


def test_effects():
    resp = {
        "ok": True,
        "receipt": {
            "tx_id": 4, "before_root": 400, "after_root": 500, "version": 8,
            "delta": {
                "actor_id": 2,
                "objects_created": [], "objects_deleted": [], "objects_frozen": [],
                "links_added": [], "links_removed": [],
                "memory_written": [], "capability_events": [],
                "effects": [("dep_notify", "deadbeef")],
            }
        }
    }
    r = parse_receipt(resp)
    assert r.delta.effects == [("dep_notify", "deadbeef")]


def test_hex_boundary():
    resp = {
        "ok": True,
        "receipt": {
            "tx_id": 5, "before_root": 500, "after_root": 600, "version": 9,
            "delta": {
                "actor_id": 0,
                "objects_created": [], "objects_deleted": [], "objects_frozen": [],
                "links_added": [], "links_removed": [],
                "memory_written": [{"object_id": 1, "state_id": 0, "value_hex": "00ff12a9"}],
                "capability_events": [], "effects": [],
            }
        }
    }
    r = parse_receipt(resp)
    assert bytes.fromhex(r.delta.memory_written[0]["value_hex"]) == b'\x00\xff\x12\xa9'


if __name__ == "__main__":
    test_empty_delta()
    test_create_object_with_write()
    test_delete_with_links()
    test_effects()
    test_hex_boundary()
    print("5 tests passed")
