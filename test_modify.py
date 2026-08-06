import sys, os, json
sys.path.insert(0, '/data/data/com.termux/files/home/forge')

project = '/data/data/com.termux/files/home/forge'
test_file = os.path.join(project, 'tests', 'real_modify_target.py')
original = 'VERSION = "1.0"\n\ndef hello():\n    return "Hello v1"\n'
os.makedirs(os.path.dirname(test_file), exist_ok=True)
with open(test_file, 'w') as f:
    f.write(original)

from forge.adapters.deepseek import DeepSeekAdapter
adapter = DeepSeekAdapter()
from forge.adapters.base import Message

prompt = "File content:\n" + original + "\nTask: change VERSION from 1.0 to 2.0 and Hello v1 to Hello v2. Output JSON with old_text matching file exactly."
response = adapter.send([Message(role='user', content=prompt)], tools=[])
raw_text = (response.content or '').strip()
if '```' in raw_text:
    parts = raw_text.split('```')
    raw_text = parts[1]
    if raw_text.startswith('json'):
        raw_text = raw_text[4:]
plan = json.loads(raw_text.strip())
print('steps:', len(plan.get('steps',[])))
for s in plan.get('steps',[]):
    old = s.get('old_text','')
    print('step:', s.get('step_id'))
    print('  old_text:', repr(old[:80]))
    print('  in file:', old in original)

from forge.world.runtime import WorldRuntime
world = WorldRuntime(project_root=project)
world.ensure_identity()

for s in plan['steps']:
    old = s['old_text']
    new = s['new_text']
    with open(test_file) as f:
        cur = f.read()
    if old in cur:
        mod = cur.replace(old, new, 1)
        with open(test_file, 'w') as f:
            f.write(mod)
        print('  modified OK')
    else:
        print('  old_text NOT FOUND in file')

with open(test_file) as f:
    final = f.read()
print()
print('final:')
print(final)
print()
print('VERSION 2.0:', '"2.0"' in final)
print('Hello v2:', 'Hello v2' in final)

world.close()
os.remove(test_file)
os.remove(os.path.join(project, 'test_modify.py'))
