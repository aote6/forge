"""Forge v2 真实代码编辑 E2E — modify 已有文件"""
import sys
import os

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


def test_real_edit():
    print("Forge v2 真实代码编辑 E2E")
    print("=" * 50)

    project = "/data/data/com.termux/files/home/forge"

    # 创建测试目标文件
    test_file = os.path.join(project, "tests", "real_edit_target.py")
    original_content = "# Test file for Forge v2 real edit\nVERSION = \"1.0\"\n\ndef hello():\n    return \"Hello v1\"\n"
    os.makedirs(os.path.dirname(test_file), exist_ok=True)
    with open(test_file, "w") as f:
        f.write(original_content)
    test("0.1 测试文件创建", os.path.exists(test_file))

    try:
        from forge.adapters.deepseek import DeepSeekAdapter
        adapter = DeepSeekAdapter()
    except Exception:
        from forge.adapters.gemini import GeminiAdapter
        adapter = GeminiAdapter()

    from forge.workspace import Workspace
    from forge.memory import MemoryStore
    from forge.runtime import Runtime

    workspace = Workspace(project_root=project)
    memory = MemoryStore()
    runtime = Runtime(adapter, workspace, memory)
    test("0.2 Runtime 初始化", True)

    task_id = "real_edit_test_001"
    runtime._task_memory.delete(task_id)

    task = f"修改 {test_file}，把 VERSION 从 '1.0' 改成 '2.0'，把 'Hello v1' 改成 'Hello v2'"

    result = runtime.run_v2(task, task_id=task_id)
    test("1. run_v2 返回", result is not None)
    test("2. 任务完成", "任务完成" in str(result))

    # 验证修改结果
    if os.path.exists(test_file):
        with open(test_file) as f:
            modified = f.read()
        test("3. VERSION 已改为 2.0", "VERSION = \"2.0\"" in modified,
             f"内容: {modified[:100]}")
        test("4. hello 已改为 Hello v2", "Hello v2" in modified)
        test("5. 文件仍是 Python 语法",
             modified.startswith("# Test file") or "def hello" in modified)

    # 清理
    if os.path.exists(test_file):
        os.remove(test_file)
    runtime._task_memory.delete(task_id)
    runtime.world.close()

    return summary()


if __name__ == "__main__":
    sys.exit(test_real_edit())
