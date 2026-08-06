"""Engineering Loop E2E — 用 EngineeringLoop 驱动完整闭环"""
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


def test_engineering_loop():
    print("=" * 50)
    print("Engineering Loop E2E")
    print("=" * 50)

    from forge.engineering import EngineeringLoop, Phase

    project = "/data/data/com.termux/files/home/forge"
    loop = EngineeringLoop(project)

    task = "在 tests/ 下创建一个 eng_loop_test.txt 文件，内容是 'Engineering Loop E2E test'"
    result = loop.run(task, task_id="eng_loop_001")

    test("1. 完成", loop.phase == Phase.COMPLETE, f"phase={loop.phase.value}")
    test("2. 有 RepoContext", loop.repo_context is not None,
         f"{len(loop.repo_context.file_tree)} files" if loop.repo_context else "N/A")
    test("3. 有 Plan", loop.plan is not None,
         f"{len(loop.plan.steps)} steps" if loop.plan else "N/A")
    test("4. 有执行结果", len(loop.execution_results) > 0)
    test("5. 结果报告", "完成" in result or "失败" in result, result[:80])

    # 清理
    test_file = os.path.join(project, "tests", "eng_loop_test.txt")
    if os.path.exists(test_file):
        os.remove(test_file)

    return summary()


if __name__ == "__main__":
    sys.exit(test_engineering_loop())
