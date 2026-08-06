"""验证适配器 — 对接 sms"""
import subprocess
import os
from forge.protocols.verification import VerificationRequest, VerificationResult
from forge.protocols.constitution import CheckStatus


def verify(request: VerificationRequest) -> VerificationResult:
    """调用 sms 进行增量构建和测试"""
    sms_main = "/data/data/com.termux/files/home/sms/main.py"

    if not os.path.exists(sms_main):
        return VerificationResult(
            status=CheckStatus.FAIL,
            executed_checks=["sms"],
            failures=[f"sms 入口不存在: {sms_main}"]
        )

    # sms 是增量构建调度器，默认跑全量模块的测试
    result = subprocess.run(
        ["python3", sms_main],
        capture_output=True, text=True, timeout=120,
        cwd="/data/data/com.termux/files/home/sms"
    )

    executed_checks = ["sms:build+test"]
    failures = []

    if result.returncode != 0:
        failures.append(f"sms 执行失败 (返回码 {result.returncode})")
        # 提取最后几行错误信息
        stderr_lines = result.stderr.strip().split("\n")[-5:]
        failures.extend(stderr_lines)

    # 检查输出中是否有测试失败标记
    output = result.stdout + result.stderr
    if "FAIL" in output or "failed" in output.lower():
        for line in output.split("\n"):
            if "FAIL" in line or "failed" in line.lower():
                failures.append(line.strip())
                if len(failures) > 10:
                    break

    status = CheckStatus.PASS if not failures else CheckStatus.FAIL
    return VerificationResult(
        status=status,
        executed_checks=executed_checks,
        failures=failures
    )
