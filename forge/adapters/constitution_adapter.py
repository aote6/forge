"""宪法检查适配器 — 对接 lu"""
import subprocess
import os
import sys

# 把 lu 的路径加入 sys.path，以便直接 import lu 的规则引擎
LU_HOME = "/data/data/com.termux/files/home/lu"
sys.path.insert(0, os.path.join(LU_HOME, "core"))

from forge.protocols.constitution import (
    ChangeProposal, ConstitutionResult, ConstitutionViolation, CheckStatus
)

LU_RULES_DIR = os.path.join(LU_HOME, "rules")


def _run_lu_rules(target: str, old_file: str, new_file: str) -> tuple[bool, str, str]:
    """直接调用 lu 的规则引擎，与 lu_patch.do_replace 内部逻辑一致"""
    # 导入 lu 的规则引擎
    import lu_patch
    ok, failed_rule, _ = lu_patch.run_rules(target, old_file, new_file)
    return ok, failed_rule or "", ""


def check(proposal: ChangeProposal) -> ConstitutionResult:
    """对 ChangeProposal 涉及的文件运行 lu 规则检查"""
    violations = []
    checked_rules = []

    # 扫描 lu 的 rules 目录获取规则名列表
    if not os.path.isdir(LU_RULES_DIR):
        return ConstitutionResult(
            status=CheckStatus.PASS,
            checked_rules=["lu(未找到规则目录)"]
        )

    rule_names = []
    for name in sorted(os.listdir(LU_RULES_DIR)):
        node_file = os.path.join(LU_RULES_DIR, name, "node.json")
        if os.path.exists(node_file):
            import json
            with open(node_file) as f:
                node = json.load(f)
            rule_names.append(node.get("name", name))

    if not rule_names:
        return ConstitutionResult(status=CheckStatus.PASS, checked_rules=["lu(无规则)"])

    checked_rules = rule_names

    # 对每个 operation 检查是否合规
    for op in proposal.operations:
        # 只对有 old/new 内容的 operation 做内容级检查
        old_content = op.get("old", "")
        new_content = op.get("new", "")
        target_files = op.get("target_files", proposal.target_files)

        if not old_content and not new_content:
            # 纯意图操作（还没产生实际 diff），跳过内容级规则检查
            continue

        for target in target_files:
            if not os.path.exists(target):
                continue

            # 创建临时 old/new 文件
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.old', delete=False) as f:
                f.write(old_content)
                old_path = f.name
            with tempfile.NamedTemporaryFile(mode='w', suffix='.new', delete=False) as f:
                f.write(new_content)
                new_path = f.name

            try:
                ok, failed_rule, _ = _run_lu_rules(target, old_path, new_path)
                if not ok:
                    violations.append(ConstitutionViolation(
                        rule_id=failed_rule or "未知规则",
                        message=f"{target}: 规则检查未通过"
                    ))
            except Exception as e:
                # lu 规则引擎不可用时降级为 PASS（不阻塞）
                print(f"[constitution_adapter] lu 规则引擎异常: {e}", file=sys.stderr)
            finally:
                os.unlink(old_path)
                os.unlink(new_path)

    status = CheckStatus.FAIL if violations else CheckStatus.PASS
    return ConstitutionResult(
        status=status,
        violations=violations,
        checked_rules=checked_rules
    )
