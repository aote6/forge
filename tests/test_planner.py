"""Planner E2E 测试 — 用真实 LLM 生成 Plan"""
import sys
import os

sys.path.insert(0, '/data/data/com.termux/files/home/forge')

from forge.contracts.repository import RepoContext
from forge.adapters.repo_adapter import get_repo_context

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

def test_planner():
    print("Planner E2E 测试")
    print("=" * 50)

    project = "/data/data/com.termux/files/home/forge"

    # 1. 获取 RepoContext
    print("\n─── 获取仓库上下文 ───")
    ctx = get_repo_context(project)
    test("1. RepoContext 获取", ctx is not None and len(ctx.file_tree) > 0,
         f"文件数: {len(ctx.file_tree)}")

    # 2. 初始化 Planner
    print("\n─── 初始化 Planner ───")
    # 用 DeepSeek adapter
    try:
        from forge.adapters.deepseek import DeepSeekAdapter
        adapter = DeepSeekAdapter()
        test("2.1 DeepSeek adapter 初始化", True)
    except Exception as e:
        # fallback to Gemini
        try:
            from forge.adapters.gemini import GeminiAdapter
            adapter = GeminiAdapter()
            test("2.1 Gemini adapter 初始化", True)
        except Exception as e2:
            test("2.1 Adapter 初始化", False, f"DeepSeek: {e}, Gemini: {e2}")
            return summary()

    from forge.planner import Planner
    planner = Planner(adapter)
    test("2.2 Planner 初始化", True)

    # 3. 生成 Plan
    print("\n─── 生成 Plan ───")
    task = "给项目增加一个 changelog 功能，记录每次修改"
    try:
        plan = planner.plan(task, ctx)
        test("3.1 Plan 生成", plan is not None and len(plan.steps) > 0,
             f"目标: {plan.goal}, 步骤数: {len(plan.steps)}")

        if plan.steps:
            print("\n  📋 生成的计划:")
            for i, step in enumerate(plan.steps, 1):
                deps = f" (依赖: {', '.join(step.dependencies)})" if step.dependencies else ""
                print(f"  {i}. [{step.operation_type}] {step.description}")
                print(f"     文件: {', '.join(step.target_files)}{deps}")

        test("3.2 Plan 有 goal", bool(plan.goal))
        test("3.3 Plan 有 steps", len(plan.steps) > 0)
        test("3.4 每个 step 有 target_files",
             all(len(s.target_files) > 0 for s in plan.steps))
        test("3.5 operation_type 合法",
             all(s.operation_type in ("modify", "create_file", "delete_file")
                 for s in plan.steps))

    except Exception as e:
        test("3.x Plan 生成失败", False, str(e))
        import traceback
        traceback.print_exc()

    return summary()


if __name__ == "__main__":
    sys.exit(test_planner())
