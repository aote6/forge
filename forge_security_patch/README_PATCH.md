# Forge 安全加固补丁

## 替换说明（在你的 forge 项目根目录执行）

```bash
# 1. 备份
cp forge/core/security.py forge/core/security.py.bak
cp forge/core/sanitizer.py forge/core/sanitizer.py.bak
cp forge/workspace.py forge/workspace.py.bak
cp forge/tools/local_tools.py forge/tools/local_tools.py.bak
cp forge/tools/display.py forge/tools/display.py.bak

# 2. 替换（把本目录文件拷进去）
cp security.py      forge/core/security.py
cp sanitizer.py     forge/core/sanitizer.py
cp workspace.py     forge/workspace.py
cp local_tools.py   forge/tools/local_tools.py
cp display.py       forge/tools/display.py

# 3. 测试（可选）
mkdir -p tests
cp test_security.py tests/test_security.py

# 4. 验证
python3 -c "from forge.core.security import is_dangerous_command; print(is_dangerous_command('cat ~/.ssh/id_rsa'))"
# 应输出：读取敏感文件 或 命令参数含敏感路径
python3 -m pytest tests/test_security.py -q
```

## 本补丁覆盖的加固点

1. **路径解析强制 resolve()**：跟随 symlink，再 relative_to，防 workspace 逃逸
2. **命令黑名单扩展**：env/printenv/declare/$VAR、敏感路径 cat/xxd/base64、python -c open/environ、grep 敏感词
3. **参数级敏感路径扫描**：即使写成 `cat $HOME/.ssh/...` 也会拦
4. **redact_secrets 扩展**：sk-/sk-proj-/sk-ant-/hf_/AIza/AKIA/JWT/PEM 头
5. **中文 prompt injection 句式**
6. **local_tools read_file/search_code** 统一走 resolve_workspace_path
7. **run_command** 返回具体拦截原因
8. **回归测试** tests/test_security.py

## 文件清单

| 补丁内文件 | 项目内目标路径 |
|------------|----------------|
| security.py | forge/core/security.py |
| sanitizer.py | forge/core/sanitizer.py |
| workspace.py | forge/workspace.py |
| local_tools.py | forge/tools/local_tools.py |
| display.py | forge/tools/display.py |
| test_security.py | tests/test_security.py |
| .gitignore | .gitignore（参考合并 .env / *.pem 等） |

注意：黑名单无法 100% 防住所有 shell 技巧；路径层 + 命令层 + 输出脱敏是多层防护。
