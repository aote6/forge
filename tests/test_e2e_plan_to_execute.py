"""Forge v2 完整闭环 E2E: Plan → Proposal → lu → Veritas → sms"""
import sys
import os

sys.path.insert(0, '/data/data/com.termux/files/home/forge')

from forge.contracts.repository import RepoContext
from forge.contracts.planning import Plan, PlanStep
from forge.contracts.constitution import ChangeProposal, CheckStatus
from forge.contracts.verification import VerificationRequest

from forge.adapters.repo_adapter import get_repo_context
from forge.adapters.constitution_adapter import check as constitution_check
from forge.adapters.verifier_adapter import verify as sms_verify
from forge.planner import plan_to_proposals

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
    for name, ok, detail in results:
        if not ok:
            print(f"  ❌ {name}: {detail}")
    print(f"{'='*50}")
    return 0 if passed == total else 1


def test_plan_to_execute():
    print("Forge v2 完整闭环: Plan → Execute")
    print("=" * 50)

    project = "/data/data/com.termux/files/home/forge"

    # ── 1. 用真实 LLM 生成 Plan ──
    print("\n─── 1. 生成 Plan ───")
    ctx = get_repo_context(project)
    test("1.1 RepoContext", ctx is not None and len(ctx.file_tree) > 0)

    try:
        from forge.adapters.deepseek import DeepSeekAdapter
        adapter = DeepSeekAdapter()
    except Exception:
        from forge.adapters.gemini import GeminiAdapter
        adapter = GeminiAdapter()

    from forge.planner import Planner
    planner = Planner(adapter)

    plan = planner.plan("在 tests/ 下创建一个 e2e_plan_test.txt 文件，内容是 'Plan to Execute test'", ctx)
    test("1.2 Plan 生成", len(plan.steps) > 0,
         f"{len(plan.steps)} 步骤: {[s.step_id for s in plan.steps]}")

    # ── 2. Plan → ChangeProposals ──
    print("\n─── 2. Plan → Proposals ───")
    proposals = plan_to_proposals(plan)
    test("2.1 Proposal 转换", len(proposals) == len(plan.steps),
         f"{len(proposals)} 个 Proposal")
    test("2.2 Proposal 结构完整",
         all(p.proposal_id and p.target_files for p in proposals))

    # ── 3. Constitution Check ──
    print("\n─── 3. Constitution Check ──")
    all_checks_pass = True
    for i, proposal in enumerate(proposals):
        result = constitution_check(proposal)
        passed = result.status == CheckStatus.PASS
        if not passed:
            all_checks_pass = False
        print(f"  {'✅' if passed else '❌'} Proposal {i+1}: {proposal.target_files} — {result.status.value}")
        if result.violations:
            for v in result.violations:
                print(f"    违规: {v.rule_id}: {v.message[:80]}")
    test("3.1 全部 Constitution PASS", all_checks_pass)

    # ── 4. Veritas Transaction ──
    print("\n─── 4. Veritas Transaction ──")
    try:
        from forge.world.runtime import WorldRuntime
        world = WorldRuntime(project_root=project)
        world.ensure_identity()

        for i, proposal in enumerate(proposals):
            for target in proposal.target_files:
                full_path = os.path.join(project, target)
                test_content = f"# E2E Plan to Execute test\n# proposal: {proposal.proposal_id}\n"

                session = world.begin_session()
                obj_id = session.create_object()
                session.write(obj_id, 0, value=full_path)
                session.write(obj_id, 1, value=test_content)
                receipt, delta = world.commit_session()

                # 补 delta 信息（已知 veritasd 限制）
                delta.memory_written = [
                    {"object_id": obj_id, "state_id": 0, "value_hex": full_path.encode().hex()},
                    {"object_id": obj_id, "state_id": 1, "value_hex": test_content.encode().hex()},
                ]
                delta.objects_created = [obj_id]

                from forge.projections.base import ProjectionManager
                from forge.projections.file_projection import FileProjection
                pm = ProjectionManager()
                pm.register(FileProjection(
                    project_root=project,
                    object_path_map=getattr(world, '_path_map', None)
                ))
                proj_results = pm.project(receipt, delta)
                all_ok = all(r.success for r in proj_results)

                file_exists = os.path.exists(full_path)
                if file_exists:
                    os.remove(full_path)

                test(f"4.{i+1} {target}",
                     all_ok and file_exists,
                     f"tx={receipt.tx_id} v={receipt.version}")

        world.close()
    except Exception as e:
        test("4.x Veritas 异常", False, str(e))
        import traceback
        traceback.print_exc()

    # ── 5. SMS Verify ──
    print("\n─── 5. SMS Verify ──")
    all_files = [f for p in proposals for f in p.target_files]
    vreq = VerificationRequest(changed_files=all_files, change_type="create_file")
    vresult = sms_verify(vreq)
    test("5.1 SMS 验证完成", vresult is not None)

    return summary()


if __name__ == "__main__":
    sys.exit(test_plan_to_execute())
