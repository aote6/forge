"""安全策略：路径黑名单 + 命令黑名单 + Git 保护

设计原则：
- 路径：先 expanduser + resolve（跟随 symlink），再 relative_to 防逃逸，再黑名单
- 命令：黑名单兜底危险操作；敏感读操作尽量拦
- 无法 100% 防住所有 shell 技巧，关键是多层（路径层 + 命令层 + 输出脱敏）
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# ── 路径黑名单（命中即拒绝）────────────────────────────────────────
BLOCKED_PATH_PATTERNS = [
    r"\.ssh/",
    r"\.gnupg/",
    r"id_rsa",
    r"id_ed25519",
    r"id_ecdsa",
    r"id_dsa",
    r"\.pem$",
    r"\.key$",
    r"\.p12$",
    r"\.pfx$",
    r"\.git-credentials",
    r"\.netrc",
    r"\.bashrc$",
    r"\.zshrc$",
    r"\.profile$",
    r"\.bash_history",
    r"\.zsh_history",
    r"\.aws/",
    r"\.config/gcloud/",
    r"\.config/gh/",
    r"\.docker/config\.json",
    r"\.kube/config",
    r"\.npmrc",
    r"\.pypirc",
    r"\.cargo/credentials",
    r"authorized_keys",
    r"known_hosts",
    r"\.env$",
    r"\.env\.",
    r"secrets?[/_-]",
    r"\.gitconfig$",
    r"termux\.properties",
    r"private[_-]?key",
    r"credentials\.json",
    r"service[_-]?account",
]

# 敏感路径片段（用于命令参数扫描）
_SENSITIVE_PATH_HINTS = re.compile(
    r"(?i)("
    r"\.ssh/|id_rsa|id_ed25519|id_ecdsa|authorized_keys|"
    r"\.gnupg/|secring|"
    r"\.aws/|\.config/gcloud|\.docker/config|\.kube/|"
    r"\.npmrc|\.pypirc|\.netrc|\.git-credentials|"
    r"\.env\b|\.pem\b|\.key\b|"
    r"\.bashrc|\.zshrc|\.profile|"
    r"/etc/passwd|/etc/shadow|/etc/sudoers"
    r")"
)

# ── 危险命令黑名单 ────────────────────────────────────────────────
DANGEROUS_COMMAND_PATTERNS = [
    (r"curl\s+.*\|\s*(ba)?sh", "curl|bash"),
    (r"wget\s+.*-O\s*-\s*\|\s*(ba)?sh", "wget|bash"),
    (r"curl\s+.*\|\s*python", "curl|python"),
    (r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|--force).*(\s/|\s~|\s\$HOME|\s\.\.)", "危险 rm"),
    (r"\brm\s+-rf\s+~", "rm -rf ~"),
    (r"\brm\s+-rf\s+/", "rm -rf /"),
    (r"\brm\s+-rf\s+\$HOME", "rm -rf $HOME"),
    (r"\bgit\s+push\s+.*--force", "git push --force"),
    (r"\bgit\s+push\s+.*\s-f\b", "git push -f"),
    (r">\s*/dev/sd[a-z]", "覆写磁盘"),
    (r"\bdd\s+.*\bof=/dev/", "dd写磁盘"),
    (r"\bchmod\s+(-R\s+)?777\s+/", "chmod 777 /"),
    (r"\bchown\s+-R\s+\S+\s+/", "chown -R /"),
    (r"\bmkfs\.", "mkfs"),
    (r":\(\)\s*\{\s*:\|:&\s*\}\s*;", "fork bomb"),
    (r"(^|[\s;|&])\benv\b", "读取环境变量 env"),
    (r"\bprintenv\b", "读取环境变量 printenv"),
    (r"\bdeclare\s+-p\b", "读取环境变量 declare"),
    (r"\bexport\s+-p\b", "读取环境变量 export -p"),
    (r"\bset\s*$", "读取环境变量 set"),
    (r"\bcompgen\s+-v\b", "读取环境变量 compgen"),
    (r"\$\{?[A-Z][A-Z0-9_]{2,}\b", "读取环境变量 $VAR"),
    (
        r"\b(cat|head|tail|less|more|xxd|od|strings|nl|tac)\s+.*("
        r"\.ssh/|id_rsa|id_ed25519|id_ecdsa|authorized_keys|"
        r"\.gnupg/|\.pem\b|\.key\b|"
        r"\.aws/|\.config/gcloud|\.docker|\.kube/|"
        r"\.npmrc|\.pypirc|\.netrc|\.git-credentials|"
        r"\.bashrc|\.zshrc|\.profile|\.env\b"
        r")",
        "读取敏感文件",
    ),
    (r"\b(base64|xxd|od|hexdump)\s+.*(\.ssh/|id_rsa|\.pem|\.key\b|\.gnupg/)", "编码读取密钥"),
    (
        r"\bgrep\s+(-[a-zA-Z]*[rR][a-zA-Z]*\s+|[^\n]*\s+)?['\"]?(API[_-]?KEY|SECRET|TOKEN|PASSWORD|DEEPSEEK|OPENAI|GEMINI|AWS_SECRET)",
        "搜索敏感关键词",
    ),
    (
        r"""\b(python3?|perl|ruby|node|php)\s+(-c|-e)\s+['"].*(open\s*\(|read\s*\(|Path\s*\(|\.read_text)""",
        "解释器读取文件",
    ),
    (
        r"""\b(python3?|perl|ruby)\s+(-c|-e)\s+['"].*(os\.environ|getenv|environ\[)""",
        "解释器读取环境变量",
    ),
]

def is_blocked_path(path: str) -> str | None:
    """返回命中的规则字符串，未命中返回 None。使用 expanduser + resolve 跟随符号链接。"""
    try:
        expanded = str(Path(path).expanduser().resolve(strict=False))
    except (OSError, RuntimeError):
        expanded = str(Path(path).expanduser())
    for pattern in BLOCKED_PATH_PATTERNS:
        if re.search(pattern, expanded, re.IGNORECASE):
            return pattern
    return None


def is_dangerous_command(cmd: str) -> str | None:
    """检测危险/敏感命令。返回原因描述或 None。"""
    if not cmd or not cmd.strip():
        return None
    normalized = " ".join(cmd.split())
    for pattern, desc in DANGEROUS_COMMAND_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return desc
    if _SENSITIVE_PATH_HINTS.search(normalized):
        if re.search(
            r"\b(cat|head|tail|less|more|xxd|od|strings|base64|hexdump)\b",
            normalized,
            re.IGNORECASE,
        ):
            return "命令参数含敏感路径"
    return None


class PathSecurityError(PermissionError):
    """Raised when a path escapes workspace or hits a blocked pattern."""


def resolve_workspace_path(project_root: str, relative_or_abs: str) -> str:
    """解析路径并强制落在 project_root 内；拒绝 ../、symlink 逃逸、黑名单。"""
    root = Path(project_root).expanduser().resolve(strict=False)
    raw = Path(os.path.expanduser(str(relative_or_abs)))
    candidate = raw if raw.is_absolute() else (root / raw)
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as e:
        raise PathSecurityError(f"无法解析路径: {relative_or_abs}: {e}") from e

    try:
        resolved.relative_to(root)
    except ValueError as e:
        raise PathSecurityError(
            f"路径逃逸 workspace: {relative_or_abs} -> {resolved} (root={root})"
        ) from e

    blocked = is_blocked_path(str(resolved))
    if blocked:
        raise PathSecurityError(
            f"路径被安全策略拦截（命中规则: {blocked}）: {resolved}"
        )
    return str(resolved)
