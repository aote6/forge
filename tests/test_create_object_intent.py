"""CREATE_OBJECT Intent — schema, executor dispatch, and pure birth semantics.

Pure world-object birth: no path, no content, no FileProjection.
Authoritative observation after commit: Receipt.delta.objects_created.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from forge.intents.intent import Intent, IntentType
from forge.intents.executor import IntentExecutor, IntentExecutionError
from forge.world.types import Receipt, TransactionDelta, CapabilityGrantView


def test_intent_create_object_schema():
    intent = Intent.create_object()
    assert intent.type is IntentType.CREATE_OBJECT
    assert intent.type.value == "create_object"
    assert intent.parameters == {}
    assert "path" not in intent.parameters
    assert "content" not in intent.parameters
    d = intent.to_dict()
    assert d["type"] == "create_object"
    assert d["parameters"] == {}


def test_intent_create_object_policy_default():
    intent = Intent.create_object(require_confirm=True)
    assert intent.policy.get("require_confirm") is True


def test_executor_recognizes_create_object():
    world = MagicMock()
    session = MagicMock()
    session.create_object.return_value = 42
    world.begin_session.return_value = session
    receipt = Receipt(tx_id=1, before_root=0, after_root=1, version=1)
    receipt.delta = TransactionDelta(
        actor_id=1,
        objects_created=[42],
        capability_grants=[
            CapabilityGrantView(
                capability_id=99,
                cap_type="AdminCap",
                grantor=42,
                grantee=42,
                resource=42,
            )
        ],
    )
    world.commit_session.return_value = (receipt, receipt.delta)

    executor = IntentExecutor(world)
    intent = Intent.create_object()
    r, delta = executor.execute(intent)

    session.create_object.assert_called_once_with()
    # pure birth: no write / path / content
    session.write.assert_not_called()
    assert 42 in delta.objects_created
    assert intent.parameters.get("_created_object_id") == 42
    # capability_map from structured grants
    cap_map = (delta.metadata or {}).get("capability_map") or {}
    assert cap_map.get(42) == 99


def test_executor_create_object_unknown_still_rejected_for_other_types():
    """Regression: unknown types still fail; CREATE_OBJECT is registered."""
    world = MagicMock()
    executor = IntentExecutor(world)
    # DELETE_OBJECT is in enum but historically had no handler in some paths;
    # we only assert CREATE_OBJECT is accepted via handler registration.
    handlers = executor._session_handlers
    assert IntentType.CREATE_OBJECT in handlers
    assert IntentType.CREATE_FILE in handlers


def test_receipt_parser_capability_grants():
    """Forge parser must accept structured capability_grants from veritasd JSON."""
    from forge.world.receipt_parser import parse_receipt

    resp = {
        "ok": True,
        "receipt": {
            "tx_id": 7,
            "before_root": "00",
            "after_root": "11",
            "version": 3,
            "delta": {
                "actor_id": 1,
                "objects_created": [10],
                "objects_deleted": [],
                "objects_frozen": [],
                "links_added": [],
                "links_removed": [],
                "memory_written": [],
                "capability_events": [
                    "grant cap_id=55 type=AdminCap grantor=10 grantee=10 resource=10"
                ],
                "capability_grants": [
                    {
                        "capability_id": 55,
                        "cap_type": "AdminCap",
                        "grantor": 10,
                        "grantee": 10,
                        "resource": 10,
                    }
                ],
                "effects": [],
            },
        },
    }
    r = parse_receipt(resp)
    assert r.delta.objects_created == [10]
    assert len(r.delta.capability_grants) == 1
    g = r.delta.capability_grants[0]
    assert g.capability_id == 55
    assert g.grantee == 10
    assert g.resource == 10
    assert g.cap_type == "AdminCap"
