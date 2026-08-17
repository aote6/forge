"""P8: Constitution must distinguish runtime create_object from mutation operations."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.adapters.constitution import check
from forge.protocols.models import ChangeProposal, CheckStatus


def _proposal(operations: list) -> ChangeProposal:
    return ChangeProposal(
        proposal_id="p8",
        plan_id="pl",
        target_files=[],
        operations=operations,
    )


class TestConstitutionRuntimeOperation:
    """Pure create_object passes without source content."""

    def test_pure_create_object_passes_without_content(self):
        p = _proposal([{"type": "create_object", "target_files": []}])
        result = check(p, project_root=".")
        assert result.status == CheckStatus.PASS, (
            f"pure create_object should PASS, got {result.status}: {result.violations}"
        )

    def test_modify_without_content_still_fails(self):
        p = _proposal([{"type": "modify", "target_files": ["a.py"]}])
        result = check(p, project_root=".")
        assert result.status == CheckStatus.FAIL, "modify without content must FAIL"
        assert any(v.rule_id == "forge.content_required" for v in result.violations), (
            f"expected content_required violation, got {result.violations}"
        )

    def test_mixed_create_object_plus_modify_without_content_fails(self):
        p = _proposal([
            {"type": "create_object", "target_files": []},
            {"type": "modify", "target_files": ["a.py"]},
        ])
        result = check(p, project_root=".")
        assert result.status == CheckStatus.FAIL, (
            "mixed with modify lacking content must FAIL"
        )

    def test_create_object_with_nonempty_targets_not_rejected_by_constitution(self):
        # Constitution doesn't check target_files; Validator does. This just
        # confirms the runtime check looks at type, not targets.
        p = _proposal([{"type": "create_object", "target_files": ["x.py"]}])
        result = check(p, project_root=".")
        # Validator would reject this, but Constitution only checks type.
        assert result.status == CheckStatus.PASS


if __name__ == "__main__":
    import unittest
    unittest.main()
