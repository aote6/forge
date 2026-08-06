"""Hub Adapter — 通过 Hub 节点调用外部工具

统一调用协议：stdin 传 JSON → subprocess 跑节点 entry → stdout 读 JSON 结果
"""
import subprocess
import json
import os
import sys

HUB_HOME = os.path.expanduser("~/hub")
NODES_DIR = os.path.join(HUB_HOME, "nodes")


def _call_node(node_name: str, request: dict, timeout: int = 60) -> dict:
    """调用 Hub 节点，stdin 传 JSON，stdout 读 JSON"""
    node_dir = os.path.join(NODES_DIR, node_name)
    node_json = os.path.join(node_dir, "node.json")

    if not os.path.exists(node_json):
        return {"error": f"节点不存在: {node_name}"}

    with open(node_json) as f:
        node = json.load(f)

    entry = os.path.join(node_dir, node.get("entry", "main.py"))
    if not os.path.exists(entry):
        return {"error": f"节点入口不存在: {entry}"}

    input_json = json.dumps(request, ensure_ascii=False)

    print(f"  [Hub] → {node_name}: {request.get('action', '?')}", file=sys.stderr)
    result = subprocess.run(
        ["python3", entry],
        input=input_json,
        capture_output=True, text=True,
        timeout=timeout,
        cwd=node_dir
    )

    if result.returncode != 0:
        return {"error": f"节点执行失败: {result.stderr[:200]}"}

    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {"error": f"节点返回非 JSON: {result.stdout[:200]}"}


def repo_context(project_path: str) -> dict:
    """调用 zhiwang 节点获取仓库上下文"""
    return _call_node("zhiwang", {"action": "snapshot", "project": project_path})


def constitution_check(target: str, old_text: str, new_text: str,
                       start_line: int = None, end_line: int = None) -> dict:
    """调用 lu 节点进行宪法检查"""
    return _call_node("lu", {
        "action": "check",
        "target": target,
        "old_text": old_text,
        "new_text": new_text,
        "start_line": start_line,
        "end_line": end_line
    })


def lu_patch(target: str, old_text: str, new_text: str,
             start_line: int = None, end_line: int = None) -> dict:
    """调用 lu 节点安全写入"""
    return _call_node("lu", {
        "action": "patch",
        "target": target,
        "old_text": old_text,
        "new_text": new_text,
        "start_line": start_line,
        "end_line": end_line
    })


def lu_create(target: str, content: str) -> dict:
    """调用 lu 节点创建文件"""
    return _call_node("lu", {
        "action": "create",
        "target": target,
        "new_text": content
    })


def lu_delete(target: str) -> dict:
    """调用 lu 节点删除文件"""
    return _call_node("lu", {
        "action": "delete",
        "target": target
    })


def sms_verify(changed_files: list, change_type: str = "modify") -> dict:
    """调用 sms 节点验证"""
    return _call_node("sms", {
        "action": "verify",
        "changed_files": changed_files,
        "change_type": change_type
    })
