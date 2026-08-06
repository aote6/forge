"""Forge v2 Runtime Engineering Loop E2E — 自动走完 6 Phase"""
import sys
import os
import shutil

sys.path.insert(0, '/data/data/com.termux/files/home/forge')

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


def test_runtime_v2():
    print("Forge v2 Runtime Engineering Loop E2E")
    print("=" * 50)

    project = "/data/data/com.termux/files/home/forge"

    # 初始化 Runtime
    print("\n─── 初始化 Runtime ───")
    try:
        from forge.adapters.deepseek import DeepSeekAdapter
        adapter = DeepSeekAdapter()
    except Exception:
        from forge.adapters.gemini import GeminiAdapter
        adapter = GeminiAdapter()

    from forge.workspace import Workspace
    from forge.memory import MemoryStore

    workspace = Workspace(project_root=project)
    memory = MemoryStore()

    from forge.runtime import Runtime
    runtime = Runtime(adapter, workspace, memory)
    test("1. Runtime 初始化", runtime is not None)

    # 跑 Engineering Loop
    print("\n─── 跑 Engineering Loop ───")
    task_id = "e2e_runtime_test_001"
    task = "在 tests/ 下创建一个 runtime_e2e_test.txt 文件，内容是 'Runtime v2 E2E test'"

    # 清理旧 checkpoint
    runtime._task_memory.delete(task_id)

    result = runtime.run_v2(task, task_id=task_id)
    test("2.1 run_v2 返回", result is not None and len(result) > 0)
    test("2.2 任务完成", "任务完成" in result or "DONE" in str(runtime.phase))

    # 验证 TaskCheckpoint 持久化
    print("\n─── 验证 TaskCheckpoint ───")
    saved = runtime._task_memory.load(task_id)
    test("3.1 Checkpoint 已保存", saved is not None,
         f"phase={saved.phase if saved else 'N/A'}")
    test("3.2 Phase 是 done", saved is not None and saved.phase == "done",
         f"phase={saved.phase if saved else 'N/A'}")

    # 列出所有任务
    tasks = runtime._task_memory.list_tasks()
    test("3.3 任务列表", len(tasks) > 0, f"{len(tasks)} 个任务")

    # 验证 Plan
    print("\n─── 验证 Plan ───")
    test("4.1 Plan 非空", runtime._plan is not None)
    if runtime._plan:
        test("4.2 Plan 有步骤", len(runtime._plan.steps) > 0,
             f"{len(runtime._plan.steps)} 步骤")
        test("4.3 Plan 有 goal", bool(runtime._plan.goal))

    # 验证文件落地
    print("\n─── 验证文件 ───")
    test_file = os.path.join(project, "tests", "runtime_e2e_test.txt")
    exists = os.path.exists(test_file)
    test("5.1 文件存在", exists)

    # 清理
    if exists:
        os.remove(test_file)
    runtime._task_memory.delete(task_id)
    runtime.world.close()

    return summary()


if __name__ == "__main__":
    sys.exit(test_runtime_v2())
