import sys, os
sys.path.insert(0, '/data/data/com.termux/files/home/forge')
from forge.engineering import EngineeringLoop

loop = EngineeringLoop('/data/data/com.termux/files/home/forge')

# 手动逐步执行
print('=== Phase 1: understand ===')
loop._do_understand()
print(f'repo files: {len(loop.repo_context.file_tree)}')

print('\n=== Phase 2: plan ===')
ok = loop._do_plan()
print(f'plan ok: {ok}')
if loop.plan:
    for s in loop.plan.steps:
        print(f'  step: {s.operation_type} {s.target_files}')

print('\n=== Phase 3: review ===')
ok = loop._do_review()
print(f'review ok: {ok}, proposals: {len(loop.proposals)}')
for p in loop.proposals:
    for op in p.operations:
        print(f'  op: type={op.get("type")}, content={repr(op.get("content","?"))[:80]}, target={p.target_files}')

print('\n=== Phase 4: execute ===')
ok = loop._do_execute()
print(f'execute ok: {ok}, results: {len(loop.execution_results)}')
for r in loop.execution_results:
    print(f'  {r["file"]} tx_id={r["tx_id"]}')

# 检查文件
for p in loop.proposals:
    for f in p.target_files:
        path = os.path.join(loop.project_root, f)
        print(f'\n文件: {path}')
        print(f'  存在: {os.path.exists(path)}')
        if os.path.exists(path):
            with open(path) as fh:
                print(f'  内容: {repr(fh.read()[:100])}')
                print(f'  大小: {os.path.getsize(path)}')
