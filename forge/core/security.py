"""安全策略：路径黑名单 + 危险命令黑名单"""
import re
from pathlib import Path

BLOCKED_PATH_PATTERNS = [
    r"\.ssh/",
    r"\.gnupg/",
    r"id_rsa", r"id_ed25519", r"\.pem$", r"\.key$",
    r"\.git-credentials", r"\.netrc",
    r"\.bashrc$", r"\.zshrc$", r"\.profile$",  # 明文密钥常写在这里
    r"\.aws/", r"\.config/gcloud/",
    r"\.docker/config\.json", r"\.npmrc", r"\.pypirc",
    r"authorized_keys", r"known_hosts",
    r"\.env$", r"\.env\.",  # .env / .env.local 等
    r"secrets?[/_-]",
    r"\.gitconfig$",  # 可能含token
    r"termux\.properties",
]

DANGEROUS_CMD_PATTERNS = [
    r"\brm\s+-rf\s+/", r"\brm\s+-rf\s+~",
    r"\bdd\s+if=", r"\bmkfs\.",
    r">\s*/dev/sd", r">\s*/dev/mmcblk",
    r"curl.*\|\s*(ba)?sh", r"wget.*\|\s*(ba)?sh",
    r"\bgit\s+push\s+.*--force", r"\bgit\s+push\s+.*-f\b",
    r"\bchmod\s+-R\s+777",
    r"\bsudo\b", r"\bsu\b",
    r"\bcat\s+.*\.ssh", r"\bcat\s+.*id_rsa",
    r"\benv\b\s*$", r"\bprintenv\b",  # 防止直接读环境变量泄露key
]


def is_blocked_path(path: str) -> str | None:
    p = str(Path(path))
    for pat in BLOCKED_PATH_PATTERNS:
        if re.search(pat, p, re.IGNORECASE):
            return pat
    return None


def is_dangerous_command(cmd: str) -> str | None:
    for pat in DANGEROUS_CMD_PATTERNS:
        if re.search(pat, cmd, re.IGNORECASE):
            return pat
    return None
