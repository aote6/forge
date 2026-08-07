import sys, os
sys.path.insert(0, '/data/data/com.termux/files/home/forge')

from forge.engineering import EngineeringLoop

project = '/data/data/com.termux/files/home/forge'
loop = EngineeringLoop(project)

task = '在 tests/ 下创建一个 eng_loop_verify.txt 文件，内容是 "Engineering Loop 真实验证测试"'
result = loop.run(task, task_id='verify_real_001')

print('\n=== 文件检查 ===')
test_file = os.path.join(project, 'tests', 'eng_loop_verify.txt')
if os.path.exists(test_file):
    with open(test_file) as f:
        content = f.read()
    print(f'文件存在: {test_file}')
    print(f'文件内容:\n{content}')
    print(f'内容匹配: {"Engineering Loop 真实验证测试" in content}')
else:
    print(f'文件不存在: {test_file}')

print(f'\nLu 快照:')
snap_dir = os.path.expanduser('~/lu/snapshots')
if os.path.isdir(snap_dir):
    import os as _os
    snaps = sorted([s for s in _os.listdir(snap_dir) if 'eng_loop_verify' in s])
    for s in snaps[-3:]:
        print(f'  {s}')

# 不删文件，留给手工检查
print(f'\n文件保留在: {test_file}')
