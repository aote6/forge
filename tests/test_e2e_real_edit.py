"""Forge v2 real-edit contract: Intent modify path."""
from __future__ import annotations

import ast

from forge.intents.intent import Intent


def test_modify_intent_shape():
    """Intent.modify_file carries Machine EditOp only (P0 contract)."""
    from forge.core.edit_contract import authoring_to_machine_ops

    machine = authoring_to_machine_ops([{
        "type": "replace",
        "start_line": 1,
        "end_line": 1,
        "new_text": 'MSG = "Hello v2"\n',
    }])
    intent = Intent.modify_file(
        path="target.py",
        operations=machine,
        require_confirm=False,
    )
    assert intent.type.value == "modify_file"
    assert intent.parameters["path"] == "target.py"
    op = intent.parameters["operations"][0]
    assert op["start_line"] == 0
    assert op["end_line"] == 1
    assert op["new_lines"] == ['MSG = "Hello v2"\n']
    assert "new_text" not in op


def test_version_bump_content_is_valid_python():
    original = 'MSG = "Hello v1"\n'
    updated = original.replace('MSG = "Hello v1"', 'MSG = "Hello v2"')
    ast.parse(updated)
    assert 'MSG = "Hello v2"' in updated
