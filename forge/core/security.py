"""安全策略：路径黑名单 + 命令黑名单 + Git 保护 + 白名单模式"""
import re
from pathlib import Path

BLOCKED_PATH_PATTERNS = [
    r"\.ssh/", r"\.gnupg/",
    r"id_rsa", r"id_ed25519", r"\.pem$", r"\.key$",
    r"\.git-credentials", r"\.netrc",
    r"\.bashrc$", r"\.zshrc$", r"\.profile$",
    r"\.aws/", r"\.config/gcloud/",
    r"\.docker/config\.json", r"\.npmrc", r"\.pypirc",
    r"authorized_keys", r"known_hosts",
    r"\.env$", r"\.env\.", r"secrets?[/_-]",
    r"\.gitconfig$", r"termux\.properties",
]

DANGEROUS_COMMAND_PATTERNS = [
    (r"curl.*\|\s*(ba)?sh", "curl|bash"),
    (r"wget.*-O\s*-\s*\|\s*(ba)?sh", "wget|bash"),
    (r"\brm\s+-rf\s+~", "rm -rf ~"),
    (r"\brm\s+-rf\s+/", "rm -rf /"),
    (r"\brm\s+-rf\s+\$HOME", "rm -rf $HOME"),
    (r"\bgit\s+push\s+.*--force", "git push --force"),
    (r"\bgit\s+push\s+.*-f\b", "git push -f"),
    (r">\s*/dev/sd[a-z]", "覆写磁盘"),
    (r"\bdd\s+if=.*of=/dev/sd", "dd写磁盘"),
    (r"\bchmod\s+777\s+/", "chmod 777 /"),
    (r"\bchmod\s+-R\s+777\s+/", "chmod -R 777 /"),
    (r"\bchown\s+-R\s+\S+\s+/", "chown -R /"),
    (r"\bmkfs\.", "mkfs"),
    (r":\(\)\s*\{\s*:\|:&\s*\}\s*;", "fork bomb"),
]

GIT_CONFIRM_COMMANDS = [
    r"\bgit\s+push\b(?!.*--force|.*-f)",
    r"\bgit\s+commit\b",
    r"\bgit\s+tag\b",
    r"\bgit\s+merge\b",
    r"\bgit\s+rebase\b",
    r"\bgit\s+reset\b",
    r"\bgit\s+stash\s+drop\b",
    r"\bgit\s+branch\s+-[dD]\b",
]

ALLOWED_COMMAND_PATTERNS = [
    r"^cargo\s", r"^make\s", r"^cmake\s", r"^ninja\s",
    r"^pytest\s", r"^python -m pytest\s", r"^cargo test\s",
    r"^grep\s", r"^rg\s", r"^find\s",
    r"^git\s+status\b", r"^git\s+log\b", r"^git\s+diff\b", r"^git\s+show\b",
    r"^git\s+branch\b(?!\s+-[dD])",
    r"^ls\b", r"^cat\b", r"^head\b", r"^tail\b", r"^wc\b",
    r"^file\b", r"^stat\b", r"^du\b", r"^df\b",
    r"^python\s+--version$", r"^python3\s+--version$",
    r"^node\s+--version$", r"^rustc\s+--version$",
    r"^which\s", r"^type\s", r"^uname\b", r"^whoami\b",
    r"^python3?\s+\S+\.py\b",
    r"^mkdir\s", r"^cd\s", r"^pwd\b",
    r"^pip\s+list\b", r"^pip\s+show\b", r"^npm\s+list\b", r"^npm\s+view\b",
]

def is_blocked_path(path: str) -> str | None:
    expanded = str(Path(path).expanduser().resolve())
    for pattern in BLOCKED_PATH_PATTERNS:
        if re.search(pattern, expanded):
            return pattern
    return None

def is_dangerous_command(cmd: str) -> str | None:
    for pattern, desc in DANGEROUS_COMMAND_PATTERNS:
        if re.search(pattern, cmd):
            return desc
    return None

def needs_git_confirmation(cmd: str) -> str | None:
    for pattern in GIT_CONFIRM_COMMANDS:
        if re.search(pattern, cmd):
            return pattern
    return None

def is_allowed_command(cmd: str) -> bool:
    for pattern in ALLOWED_COMMAND_PATTERNS:
        if re.search(pattern, cmd):
            return True
    return False
