"""Forge v2 E2E 测试 — 假 Planner，验证全链路"""
import sys
import os
import tempfile
import shutil

sys.path.insert(0, '/data/data/com.termux/files/home/forge')

from forge.protocols.repository import RepoContext
from forge.protocols.planning import Plan, PlanStep
from forge.protocols.constitution import ChangeProposal, ConstitutionResult, CheckStatus
from forge.protocols.verification import VerificationRequest, VerificationResult

from forge.adapters.repo_adapter import get_repo_context
from forge.adapters.constitution_adapter import check as constitution_check

PASS = 0
FAIL = 1
results = []

def test(name: str, condition: bool, detail: str = ""):
    status = "✅" if condition else "❌"
    results.append((name, condition, detail))
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))

def summary():
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'='*50}")
    print(f"结果: {passed}/{total} 通过")
    if passed == total:
        print("全链路通过 ✅")
    else:
        for name, ok, detail in results:
            if not ok:
                print(f"  ❌ {name}: {detail}")
    print(f"{'='*50}")
    return 0 if passed == total else 1

def fake_planner(task: str, repo: RepoContext) -> Plan:
    readme = None
    for f in repo.file_tree:
        if "README" in f:
            readme = f
            break
    if not readme and repo.file_tree:
        readme = repo.file_tree[0]
    target = readme or "README.md"

    return Plan(
        plan_id="fake_plan_001",
        goal=task,
        steps=[
            PlanStep(step_id="step_1", description=f"修改 {target}",
                     target_files=[target], operation_type="modify")
        ],
        assumptions=["测试环境"]
    )

def case1_normal():
    print("\n─── Case 1: 正常全链路 ───")
    ctx = get_repo_context("/data/data/com.termux/files/home/forge")
    test("1.1 RepoContext 获取", ctx is not None and len(ctx.file_tree) > 0,
         f"文件数: {len(ctx.file_tree)}")

    plan = fake_planner("修改 README", ctx)
    test("1.2 Plan 生成", plan is not None and len(plan.steps) > 0,
         f"步骤数: {len(plan.steps)}")

    # 纯意图检查：不传 old/new，Constitution adapter 应直接 PASS
    proposal = ChangeProposal(
        proposal_id=plan.plan_id,
        plan_id=plan.plan_id,
        target_files=[step.target_files[0] for step in plan.steps],
        operations=[{"type": "modify", "desc": step.description,
                     "target_files": [step.target_files[0]]}
                    for step in plan.steps],
        reason=plan.goal,
        expected_effects=["README 内容更新"]
    )
    check_result = constitution_check(proposal)
    test("1.3 Constitution 检查执行", check_result is not None,
         f"状态: {check_result.status.value}, 规则: {check_result.checked_rules}")
    # 纯意图检查应通过（没有 old/new 内容，跳过内容级规则）
    test("1.4 Constitution PASS (意图检查)", check_result.status == CheckStatus.PASS,
         f"违规数: {len(check_result.violations)}")

    print("  → Case 1 完成")

def case2_constitution_block():
    print("\n─── Case 2: Constitution 拦截 ───")
    tmpdir = tempfile.mkdtemp()
    target = os.path.join(tmpdir, "test.txt")
    with open(target, "w") as f:
        f.write("hello\nworld\nhello\n")

    # 传实际 old/new 内容，触发唯一性校验
    proposal = ChangeProposal(
        proposal_id="fake_block_001",
        plan_id="fake_block_001",
        target_files=[target],
        operations=[{
            "type": "modify",
            "old": "hello",
            "new": "replaced",
            "target_files": [target]
        }],
        reason="测试重复内容拦截",
        expected_effects=[]
    )

    check_result = constitution_check(proposal)
    test("2.1 Constitution 执行", check_result is not None,
         f"状态: {check_result.status.value}")
    test("2.2 Constitution FAIL (拦截)", check_result.status == CheckStatus.FAIL,
         f"违规: {[v.rule_id for v in check_result.violations]}")
    test("2.3 违规信息非空", len(check_result.violations) > 0,
         f"第一条: {check_result.violations[0].message[:80] if check_result.violations else 'N/A'}")

    shutil.rmtree(tmpdir)
    print("  → Case 2 完成")

def case3_protocol_completeness():
    print("\n─── Case 3: 协议完整性 ───")
    ctx = RepoContext(repo_id="test", commit_hash="abc12345",
                      file_tree=["README.md", "src/main.py"],
                      changed_files=["README.md"], recent_changes=["fix: typo"])
    test("3.1 RepoContext 构造", ctx.repo_id == "test" and len(ctx.file_tree) == 2)

    plan = Plan(plan_id="p1", goal="test",
                steps=[PlanStep(step_id="s1", description="修改文件",
                                target_files=["README.md"], operation_type="modify")])
    test("3.2 Plan 构造", plan.plan_id == "p1" and len(plan.steps) == 1)

    proposal = ChangeProposal(proposal_id="cp1", plan_id="p1",
                              target_files=["README.md"],
                              operations=[{"type": "modify"}],
                              reason="测试", expected_effects=["文件更新"])
    test("3.3 ChangeProposal 构造", proposal.proposal_id == "cp1")

    result = ConstitutionResult(status=CheckStatus.PASS, checked_rules=["唯一性校验"])
    test("3.4 ConstitutionResult 构造", result.status == CheckStatus.PASS)

    vreq = VerificationRequest(changed_files=["README.md"], change_type="modify")
    test("3.5 VerificationRequest 构造", len(vreq.changed_files) == 1)

    vres = VerificationResult(status=CheckStatus.PASS, executed_checks=["sms"])
    test("3.6 VerificationResult 构造", vres.status == CheckStatus.PASS)

    from forge.protocols.execution import TaskCheckpoint
    cp = TaskCheckpoint(task_id="t1", phase="checking", plan_id="p1", completed_steps=["s1"])
    test("3.7 TaskCheckpoint 构造", cp.phase == "checking")

    print("  → Case 3 完成")

if __name__ == "__main__":
    print("Forge v2 E2E 测试")
    print("=" * 50)
    case1_normal()
    case3_protocol_completeness()
    case2_constitution_block()
    sys.exit(summary())
