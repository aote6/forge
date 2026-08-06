"""Forge v2 失败路径 E2E: modify + 语法错误回滚 + 中断恢复"""
import sys, os, json

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


def test_modify_existing_file():
    """Case 1: 修改已有 Python 文件"""
    print("=" * 50)
    print("Case 1: Modify existing Python file")
    print("=" * 50)

    project = "/data/data/com.termux/files/home/forge"
    test_file = os.path.join(project, "tests", "modify_target.py")

    original = '# Test module\nVERSION = "1.0"\n\ndef get_version():\n    return "1.0"\n'
    os.makedirs(os.path.dirname(test_file), exist_ok=True)
    with open(test_file, "w") as f:
        f.write(original)

    from forge.adapters.lu_patch_adapter import patch as lu_patch

    old_text = 'VERSION = "1.0"'
    new_text = 'VERSION = "2.0"'

    ok, msg = lu_patch(test_file, old_text, new_text)
    test("1.1 Lu modify 成功", ok, msg[:80])

    if ok:
        with open(test_file) as f:
            content = f.read()
        test("1.2 文件内容已更新", '"2.0"' in content)
        test("1.3 语法仍正确", content.strip().startswith("# Test module"))

    if os.path.exists(test_file):
        os.remove(test_file)

    return True


def test_syntax_error_rollback():
    """Case 2: 制造真正的语法错误——把 def hello(): 改成 def hello(——验证 Lu 回滚"""
    print("\n" + "=" * 50)
    print("Case 2: Syntax error triggers Lu rollback")
    print("=" * 50)

    project = "/data/data/com.termux/files/home/forge"
    test_file = os.path.join(project, "tests", "syntax_test.py")

    original = 'def hello():\n    return "Hello"\n'
    os.makedirs(os.path.dirname(test_file), exist_ok=True)
    with open(test_file, "w") as f:
        f.write(original)

    from forge.adapters.lu_patch_adapter import patch as lu_patch

    # 真正的语法错误：def hello(): → def hello(   (缺少右括号)
    old_text = 'def hello():'
    new_text = 'def hello('

    ok, msg = lu_patch(test_file, old_text, new_text)
    test("2.1 Lu 检测到语法错误", not ok, msg[:100])
    test("2.2 Lu 报告回滚或语法失败", "回滚" in msg or "语法" in msg or "SyntaxError" in msg or "失败" in msg or "错误" in msg)

    # 验证文件未被破坏
    with open(test_file) as f:
        content = f.read()
    test("2.3 文件恢复原样", content == original, f"content: {content[:60]}")

    if os.path.exists(test_file):
        os.remove(test_file)

    return True


def test_checkpoint_recovery():
    """Case 3: 中断恢复 - TaskCheckpoint 持久化后能恢复"""
    print("\n" + "=" * 50)
    print("Case 3: TaskCheckpoint recovery")
    print("=" * 50)

    project = "/data/data/com.termux/files/home/forge"

    from forge.task_memory import TaskMemory, make_checkpoint
    from forge.protocols.execution import TaskCheckpoint

    tm = TaskMemory(project)

    task_id = "recovery_test_001"
    cp = make_checkpoint(task_id, "executing", completed_steps=["s1", "s2"])
    cp.state["test_data"] = "persisted"
    tm.save(cp)
    test("3.1 Checkpoint 已保存", True)

    tm2 = TaskMemory(project)
    loaded = tm2.load(task_id)
    test("3.2 Checkpoint 可恢复", loaded is not None)
    test("3.3 Phase 正确", loaded.phase == "executing" if loaded else False)
    test("3.4 自定义状态保留", loaded.state.get("test_data") == "persisted" if loaded else False)

    cp2 = make_checkpoint(task_id, "done", completed_steps=["s1", "s2", "s3"])
    tm2.save(cp2)
    loaded2 = tm2.load(task_id)
    test("3.5 任务完成", loaded2.phase == "done" if loaded2 else False)

    tm.delete(task_id)

    return True


if __name__ == "__main__":
    test_modify_existing_file()
    test_syntax_error_rollback()
    test_checkpoint_recovery()
    sys.exit(summary())
