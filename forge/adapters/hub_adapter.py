"""Hub Adapter v2 — 类型化 API，全部返回协议对象"""
import subprocess
import json
import os
import sys
import time

from forge.protocols.models import (
    RepoContext, ConstitutionResult, VerificationResult,
    CheckStatus, ConstitutionViolation
)

HUB_HOME = os.path.expanduser("~/hub")
NODES_DIR = os.path.join(HUB_HOME, "nodes")


def _call_node(node_name: str, action: str, payload: dict = None, timeout: int = 60) -> dict:
    """统一节点调用，返回原始 dict"""
    node_dir = os.path.join(NODES_DIR, node_name)
    node_json = os.path.join(node_dir, "node.json")
    if not os.path.exists(node_json):
        return {"passed": False, "error": f"节点不存在: {node_name}"}

    with open(node_json) as f:
        meta = json.load(f)

    entry = os.path.join(node_dir, meta.get("entry", "main.py"))
    if not os.path.exists(entry):
        return {"passed": False, "error": f"入口不存在: {entry}"}

    request = {
        "request_id": f"{node_name}_{int(time.time())}",
        "node": node_name,
        "action": action,
        "payload": payload or {}
    }

    print(f"  [Hub] -> {node_name}: {action}", file=sys.stderr)

    try:
        result = subprocess.run(
            ["python3", entry],
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True, text=True,
            timeout=timeout, cwd=node_dir
        )
    except subprocess.TimeoutExpired:
        return {"passed": False, "error": "超时"}
    except Exception as e:
        return {"passed": False, "error": str(e)}

    if result.returncode != 0:
        return {"passed": False, "error": result.stderr[:500]}

    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {"passed": False, "error": f"非JSON: {result.stdout[:200]}"}


def get_repo_context(project_path: str) -> RepoContext:
    r = _call_node("zhiwang", "snapshot", {"project": project_path})
    return RepoContext(
        repo_id=r.get("repo_id", project_path),
        commit_hash=r.get("commit_hash", ""),
        branch=r.get("branch", ""),
        file_tree=r.get("file_tree", []),
        recent_commits=r.get("recent_commits", []),
        changed_files=r.get("changed_files", []),
        status_excerpt=r.get("status_excerpt", "")
    )


def check_constitution(target: str, old_text: str, new_text: str) -> ConstitutionResult:
    r = _call_node("lu", "check", {
        "target": target, "old_text": old_text, "new_text": new_text
    })
    if r.get("passed", False):
        return ConstitutionResult(status=CheckStatus.PASS, checked_rules=r.get("checked_rules", ["lu"]))
    else:
        return ConstitutionResult(
            status=CheckStatus.FAIL,
            violations=[ConstitutionViolation(rule_id="lu", message=r.get("error", "; ".join(r.get("violations", []))))],
            checked_rules=r.get("checked_rules", ["lu"])
        )


def lu_patch(target: str, old_text: str, new_text: str,
             start_line: int = None, end_line: int = None) -> bool:
    r = _call_node("lu", "patch", {
        "target": target, "old_text": old_text, "new_text": new_text,
        "start_line": start_line, "end_line": end_line
    })
    return r.get("passed", False)


def lu_create(target: str, content: str) -> bool:
    r = _call_node("lu", "create", {"target": target, "new_text": content})
    return r.get("passed", False)


def lu_delete(target: str) -> bool:
    r = _call_node("lu", "delete", {"target": target})
    return r.get("passed", False)


def run_verification(changed_files: list, change_type: str = "modify") -> VerificationResult:
    r = _call_node("sms", "verify", {
        "changed_files": changed_files, "change_type": change_type
    })
    if r.get("passed", False):
        return VerificationResult(status=CheckStatus.PASS, executed_checks=r.get("executed_checks", ["sms"]))
    else:
        return VerificationResult(
            status=CheckStatus.FAIL,
            failures=r.get("failures", [r.get("error", "")])
        )
