"""Forge v2 real-edit contract: Intent modify path; lu_patch remains forbidden."""
from __future__ import annotations

import ast
import os
import shutil
import tempfile

import pytest

from forge.intents.intent import Intent


def test_lu_patch_forbidden_for_real_edit():
    from forge.adapters.lu_patch_adapter import LuWriteForbidden, patch as lu_patch

    root = tempfile.mkdtemp(prefix="forge_edit_")
    try:
        path = os.path.join(root, "target.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write('MSG = "Hello v1"\n')
        with pytest.raises(LuWriteForbidden):
            lu_patch(path, 'MSG = "Hello v1"', 'MSG = "Hello v2"')
        with open(path, encoding="utf-8") as f:
            assert 'MSG = "Hello v1"' in f.read()
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
