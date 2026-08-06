"""Forge v2 完整闭环: Hub + Planner + lu + Veritas + sms + Checkpoint"""
import sys, os

sys.path.insert(0, '/data/data/com.termux/files/home/forge')

results = []

def test(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    results.append((name, condition, detail))
    print(f"  {status}: {name}" + (f" - {detail}" if detail else ""))

def summary():
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'='*50}")
    print(f"Result: {passed}/{total} passed")
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL: {name}: {detail}")
    print(f"{'='*50}")
    return 0 if passed == total else 1


def test_full_loop_with_hub():
    print("=" * 50)
    print("Forge v2 完整闭环: Hub + Planner + lu + Veritas + sms")
    print("=" * 50)

    project = "/data/data/com.termux/files/home/forge"

    # Phase 1: RepoContext
    print("\n--- Phase 1: RepoContext via Hub ---")
    from forge.adapters.hub_adapter import get_repo_context
    ctx = get_repo_context(project)
    test("1.1 Hub->zhiwang", len(ctx.file_tree) > 0, f"{len(ctx.file_tree)} files")
    test("1.2 commit_hash", bool(ctx.commit_hash), ctx.commit_hash[:8] if ctx.commit_hash else "N/A")

    # Phase 2: Fake Planner
    print("\n--- Phase 2: Fake Planner ---")
    from forge.protocols.models import Plan, PlanStep
    test_file = "tests/fulllo_op_test.txt"
    plan = Plan(
        plan_id="fulllo_001",
        goal="创建测试文件验证完整链路",
        steps=[
            PlanStep(step_id="s1", target_files=[test_file],
                     operation_type="create_file",
                     description="创建测试文件")
        ]
    )
    test("2.1 Plan 生成", len(plan.steps) == 1)

    # Phase 3: Constitution Check
    print("\n--- Phase 3: Constitution Check via Hub ---")
    from forge.adapters.hub_adapter import check_constitution
    target_path = os.path.join(project, test_file)
    check_result = check_constitution(target_path, "", "# test\n")
    test("3.1 Hub->lu", check_result.status.value == "pass",
         f"rules: {check_result.checked_rules}")

    # Phase 4: Execute (Lu + Veritas)
    print("\n--- Phase 4: Execute (Lu + Veritas) ---")
    from forge.adapters.hub_adapter import lu_create
    ok = lu_create(target_path, "# Forge v2 完整闭环测试\n")
    test("4.1 Hub->lu create", ok)

    from forge.adapters.veritas_adapter import VeritasAdapter
    from forge.protocols.models import TransactionRequest
    va = VeritasAdapter(project)
    receipt = va.execute(TransactionRequest(
        request_id="fulllo_001",
        proposal_id="fulllo_001",
        files=[{"path": target_path, "content": "# test\n", "operation": "create"}]
    ))
    test("4.2 Veritas commit", receipt.success, f"tx_id={receipt.tx_id} v={receipt.version}")

    # Phase 5: Verification
    print("\n--- Phase 5: Verification via Hub ---")
    from forge.adapters.hub_adapter import run_verification
    vresult = run_verification([test_file])
    test("5.1 Hub->sms", vresult.status.value == "pass",
         f"checks: {vresult.executed_checks}")

    # Phase 6: Checkpoint
    print("\n--- Phase 6: Checkpoint ---")
    from forge.protocols.models import TaskCheckpoint
    from forge.task_memory import TaskMemory
    tm = TaskMemory(project)
    cp = TaskCheckpoint(task_id="fulllo_op_001", phase="done",
                        plan_id=plan.plan_id, completed_steps=["s1"])
    tm.save(cp)
    loaded = tm.load("fulllo_op_001")
    test("6.1 Checkpoint", loaded is not None and loaded.phase == "done")
    tm.delete("fulllo_op_001")

    # Cleanup
    if os.path.exists(target_path):
        os.remove(target_path)
    va.close()

    return summary()


if __name__ == "__main__":
    sys.exit(test_full_loop_with_hub())
