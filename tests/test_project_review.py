"""project_review fact-retrieval closure tests."""
from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from forge.tools.project_review import (
    build_review,
    load_last_test_result,
    make_project_review_tools,
    save_last_test_result,
)
from forge.tools.schemas import READ_ONLY_TOOL_DECLARATIONS
from forge.tools import make_tools
from forge.workspace import Workspace


def _git(root: Path, *args: str) -> None:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout


def _init_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")
    return root


def test_schema_declares_project_review():
    names = {d["name"] for d in READ_ONLY_TOOL_DECLARATIONS}
    assert "project_review" in names


def test_project_review_registered(tmp_path: Path):
    tools = make_tools(workspace=Workspace(project_root=str(tmp_path)), allow_mutation=False)
    assert "project_review" in tools


def test_default_since_today_and_commits(tmp_path: Path):
    root = _init_repo(tmp_path)
    today = date.today().isoformat()
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-m", "feat: today work")
    payload = build_review(str(root))
    assert payload["meta"]["since"] == today
    assert payload["meta"]["include_conversation"] is False
    assert "conversation" not in payload["context"]
    assert payload["fact"]["git"]["commit_count"] >= 1
    assert any("today work" in c["subject"] for c in payload["fact"]["git"]["commits"])
    assert payload["meta"]["inference"] is None


def test_dirty_worktree(tmp_path: Path):
    root = _init_repo(tmp_path)
    (root / "dirty.txt").write_text("x\n", encoding="utf-8")
    payload = build_review(str(root))
    assert payload["fact"]["git"]["worktree_clean"] is False


def test_status_evidence_not_fact(tmp_path: Path):
    root = _init_repo(tmp_path)
    today = date.today().isoformat()
    (root / "STATUS.md").write_text(f"## Work ({today})\n- 全量 999 passed\n", encoding="utf-8")
    payload = build_review(str(root), include_status=True)
    se = payload["evidence"]["status_md"]
    assert se["authority"] == "narrative/evidence"
    assert se["verified"] is False
    assert payload["fact"]["tests"]["status"] == "unverified"


def test_memory_marked_heuristic(tmp_path: Path):
    root = _init_repo(tmp_path)
    forge = root / ".forge"
    forge.mkdir()
    (forge / "project_memory.json").write_text(json.dumps({"last_task": "old"}), encoding="utf-8")
    mem = build_review(str(root))["context"]["project_memory"]
    assert mem["authority"] == "heuristic" and mem["may_be_stale"] is True


def test_session_only_when_requested(tmp_path: Path):
    root = _init_repo(tmp_path)
    from forge.tools import session_changes as sc
    sc.clear()
    sc.record("foo.py", tool="str_replace", summary="edit")
    assert "session_changes" not in build_review(str(root), include_session=False)["context"]
    sc2 = build_review(str(root), include_session=True)["context"]["session_changes"]
    assert sc2["calendar_scope"] is False and sc2["count"] >= 1
    sc.clear()


def test_conversation_excluded_by_default(tmp_path: Path):
    root = _init_repo(tmp_path)
    forge = root / ".forge"
    forge.mkdir()
    (forge / "conversation_log.jsonl").write_text(
        json.dumps({"role": "user", "content": "secret"}) + "\n", encoding="utf-8"
    )
    assert "conversation" not in build_review(str(root))["context"]
    assert build_review(str(root), include_conversation=True)["context"]["conversation"]["not_project_fact"]


def test_status_git_conflict_no_commits(tmp_path: Path):
    root = _init_repo(tmp_path)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    (root / "STATUS.md").write_text(f"## Ship ({tomorrow})\n- finished\n- 全部通过\n", encoding="utf-8")
    payload = build_review(str(root), since=tomorrow, include_status=True)
    assert payload["fact"]["git"]["commit_count"] == 0
    assert "status_work_without_commits" in [c["conflict_type"] for c in payload["conflicts"]]


def test_unverified_and_persisted_test(tmp_path: Path):
    root = _init_repo(tmp_path)
    assert build_review(str(root))["fact"]["tests"]["status"] == "unverified"
    save_last_test_result(str(root), {"command": "pytest", "passed": 10, "failed": 0, "returncode": 0, "status": "passed"})
    assert load_last_test_result(str(root))["passed"] == 10
    assert build_review(str(root))["fact"]["tests"]["status"] == "passed"


def test_tool_no_inference_and_callable(tmp_path: Path):
    root = _init_repo(tmp_path)
    payload = build_review(str(root))
    assert payload["meta"]["inference"] is None
    for key in ("fact", "evidence", "context", "conflicts", "meta"):
        assert key in payload
    res = make_project_review_tools(Workspace(project_root=str(root)))["project_review"]()
    assert res.success and "FACT" in res.display


def test_status_tests_claim_conflict(tmp_path: Path):
    root = _init_repo(tmp_path)
    today = date.today().isoformat()
    (root / "STATUS.md").write_text(f"## Day ({today})\n- 全量 482 passed\n", encoding="utf-8")
    (root / "x.txt").write_text("1\n", encoding="utf-8")
    _git(root, "add", "x.txt")
    _git(root, "commit", "-m", "chore")
    types = [c["conflict_type"] for c in build_review(str(root), since=today)["conflicts"]]
    assert "status_tests_unverified" in types


def test_timezone_today_string(tmp_path: Path):
    root = _init_repo(tmp_path)
    assert build_review(str(root), since="today")["meta"]["since"] == date.today().isoformat()
