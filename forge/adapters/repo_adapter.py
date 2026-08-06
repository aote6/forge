"""仓库感知适配器 — 对接 zhiwang"""
import subprocess
from forge.protocols.repository import RepoContext

ZW_HOME = "/data/data/com.termux/files/home/zhiwang"


def get_repo_context(project_path: str) -> RepoContext:
    """调用 zhiwang snapshot.sh 获取仓库上下文"""
    result = subprocess.run(
        ["bash", f"{ZW_HOME}/core/snapshot.sh", project_path, "forge"],
        capture_output=True, text=True, timeout=30
    )

    output = result.stdout

    # 解析 zhiwang 的结构化输出
    file_tree = []
    in_tree = False
    for line in output.split("\n"):
        if line.startswith("### 源码文件树 ###"):
            in_tree = True
            continue
        if line.startswith("========"):
            in_tree = False
            continue
        if in_tree and line.startswith("./"):
            file_tree.append(line[2:])  # 去掉 ./

    commit_hash = ""
    for line in output.split("\n"):
        if line.startswith("commit "):
            commit_hash = line.split()[1][:8]
            break

    return RepoContext(
        repo_id=project_path,
        commit_hash=commit_hash,
        file_tree=file_tree,
        changed_files=[],
        recent_changes=[],
        status_excerpt=output[:2000]
    )
