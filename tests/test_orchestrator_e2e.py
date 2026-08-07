"""Engineering Orchestrator E2E — 验证完整闭环"""
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


def test_orchestrator():
    print("=" * 50)
    print("Engineering Orchestrator E2E")
    print("=" * 50)

    project = "/data/data/com.termux/files/home/forge"

    from forge.world.runtime import WorldRuntime
    from forge.projections.base import ProjectionManager
    from forge.projections.file_projection import FileProjection
    from forge.projections.git_projection import GitProjection
    from forge.projections.index_projection import IndexProjection

    world = WorldRuntime(project_root=project)
    world.ensure_identity()

    projections = ProjectionManager()
    path_map = getattr(world, '_path_map', None)
    projections.register(FileProjection(project_root=project, object_path_map=path_map))
    projections.register(GitProjection(project_root=project))
    projections.register(IndexProjection(project_root=project))

    # 需要 Planner
    from forge.adapters.deepseek import DeepSeekAdapter
    from forge.planner import Planner
    adapter = DeepSeekAdapter()
    planner = Planner(adapter)

    from forge.orchestrator.engine import EngineeringOrchestrator
    orch = EngineeringOrchestrator(
        project_root=project,
        world=world,
        projections=projections,
        planner=planner
    )

    task = "在 tests/ 下创建一个 orch_e2e_test.txt 文件，内容是 'Orchestrator E2E test'"
    result = orch.run(task, task_id="orch_e2e_001")

    test("1. 完成", orch.phase.value == "complete", f"phase={orch.phase.value}")
    test("2. 结果报告", "完成" in str(result) or "complete" in str(result), str(result)[:80])

    test_file = os.path.join(project, "tests", "orch_e2e_test.txt")
    if os.path.exists(test_file):
        with open(test_file) as f:
            content = f.read()
        test("3. 文件存在", True)
        test("4. 文件有内容", len(content) > 0, f"size={len(content)}")
        test("5. 内容匹配", "Orchestrator E2E test" in content)
        os.remove(test_file)
    else:
        test("3. 文件存在", False)

    world.close()
    return summary()


if __name__ == "__main__":
    sys.exit(test_orchestrator())
