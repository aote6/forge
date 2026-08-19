"""工具声明 — LLM 可见的工具集。

READ_ONLY_TOOL_DECLARATIONS: 只读/探索工具。
MUTATION_TOOL_DECLARATIONS: World 与文件突变工具，供 Runtime 工具循环使用。
  突变经 IntentExecutor → WorldSession → Veritas（commit/abort），再投影。

TOOL_DECLARATIONS 默认 = 只读集合（兼容旧 import）。
生产 Runtime._run_conversation 使用 READ_ONLY + MUTATION。
"""

READ_ONLY_TOOL_DECLARATIONS = [
    {
        "name": "list_files",
        "description": "列出项目目录结构，用于了解项目布局。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径，默认项目根目录"},
                "depth": {"type": "integer", "description": "深度，默认2"},
            },
            "required": [],
        },
    },
    {
        "name": "read_file",
        "description": "读取文件内容。start/end 指定行范围，end=0 表示读全部。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "start": {"type": "integer", "description": "起始行号（从1开始，默认1）"},
                "end": {"type": "integer", "description": "结束行号（0=读全部，默认0）"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_code",
        "description": "在项目中搜索代码，返回匹配的文件名和行号。",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "搜索关键词或正则"},
                "path": {"type": "string", "description": "搜索路径，默认当前目录"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "git_diff",
        "description": "查看当前工作区未暂存的修改。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "world_info",
        "description": "查看 Veritas 世界摘要：版本号、state root、对象总数。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_world_objects",
        "description": "列出 Veritas 世界中的所有对象（id 和状态）。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_world_object",
        "description": "查看指定 ObjectId 的对象状态。",
        "parameters": {
            "type": "object",
            "properties": {
                "object_id": {"type": "integer", "description": "对象 ID"},
            },
            "required": ["object_id"],
        },
    },
    {
        "name": "list_world_links",
        "description": "列出 Veritas 世界中所有对象之间的链接关系（from -[type]-> to）。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "read_file_with_lines",
        "description": "读取文件并显式附带行号（如 0042 | def foo():），用于精准对齐 Planner 的 modify 参数。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "start_line": {"type": "integer", "description": "起始行（1-based，可选）"},
                "end_line": {"type": "integer", "description": "结束行（1-based，可选）"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "preview_line_mutation",
        "description": "只读模拟：预览将 start_line 到 end_line 替换为 new_text 后的上下文。不修改文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "start_line": {"type": "integer", "description": "替换起始行（1-based）"},
                "end_line": {"type": "integer", "description": "替换结束行（1-based）"},
                "new_text": {"type": "string", "description": "拟替换的新文本"},
            },
            "required": ["path", "start_line", "end_line", "new_text"],
        },
    },
    {
        "name": "get_symbol_line_range",
        "description": "查询文件内指定类或函数的精确起始行号和结束行号。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "symbol_name": {"type": "string", "description": "类名或函数名"},
            },
            "required": ["path", "symbol_name"],
        },
    },
    {
        "name": "find_symbol_definition",
        "description": "精确查找类或函数的定义位置（文件名、行号、签名、docstring）。",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol_name": {"type": "string", "description": "要查找的类名或函数名"},
            },
            "required": ["symbol_name"],
        },
    },
    {
        "name": "get_call_chain",
        "description": "查找某符号的所有直接调用者（谁调用了它）和被调用者（它调用了谁）。",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol_name": {"type": "string", "description": "函数或类名"},
            },
            "required": ["symbol_name"],
        },
    },
    {
        "name": "get_diff_summary",
        "description": "归纳 git diff 的语义变更（变更文件、新增/删除的函数和类），省 Token。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "extract_code_skeleton",
        "description": "提取 Python 文件的代码骨架（保留类/函数签名和 import，隐藏函数体，带行号）。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "git_status_enhanced",
        "description": "查看完整 Git 状态：staged、unstaged、untracked 文件，当前分支，最近提交。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_tests",
        "description": "列出项目中所有测试文件（test_*.py 或 *_test.py）。",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "搜索目录，默认项目根目录"},
            },
            "required": [],
        },
    },
    {
        "name": "read_git_version",
        "description": "读取文件在某个 git 版本（如 HEAD~1 或 commit hash）的内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "revision": {"type": "string", "description": "git 版本引用，默认 HEAD~1"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_history",
        "description": "搜索对话历史日志（.forge/conversation_log.jsonl）中的关键信息。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词或正则表达式"},
                "max_results": {"type": "integer", "description": "最多返回条数，默认 5"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "summarize_file",
        "description": "生成或读取文件的 AST 摘要（imports/classes/functions），自动缓存到 .forge/summaries/。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_repo_map",
        "description": "生成工程的代码结构图（Class/Function 签名），用极少 Token 了解代码骨架。",
        "parameters": {
            "type": "object",
            "properties": {
                "root_dir": {"type": "string", "description": "根目录路径，默认当前目录"},
                "max_tokens": {"type": "integer", "description": "最大预算 tokens（粗略估计），默认 1500"},
            },
            "required": [],
        },
    },
    {
        "name": "read_files",
        "description": "批量读取多个文件内容，支持行范围。一次调用可读多个文件，减少往返次数。",
        "parameters": {
            "type": "object",
            "properties": {
                "requests": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "文件路径"},
                            "start_line": {"type": "integer", "description": "起始行（1-based，可选）"},
                            "end_line": {"type": "integer", "description": "结束行（1-based，可选）"},
                        },
                        "required": ["path"],
                    },
                    "description": "读取文件请求列表",
                },
            },
            "required": ["requests"],
        },
    },
    {
        "name": "run_test_structured",
        "description": "运行 pytest 并返回结构化结果（通过/失败摘要、失败测试列表），不返回冗长原始日志。",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "测试文件或目录路径，默认 tests/"},
            },
            "required": [],
        },
    },
    {
        "name": "run_diagnostics",
        "description": "对 Python 文件运行 AST 语法检查，返回结构化诊断（SyntaxError 及行号）。",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "诊断目标目录，默认当前目录"},
            },
            "required": [],
        },
    },
    {
        "name": "get_context_budget",
        "description": "估算当前已跟踪文件的 Token 占用，帮助决定是否需要压缩上下文。",
        "parameters": {
            "type": "object",
            "properties": {
                "tracked_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "已读取或关注的文件列表",
                },
            },
            "required": [],
        },
    },
    {
        "name": "inspect_last_intent",
        "description": "查看上一次 Veritas 事务提交的执行结果（tx_id、version、对象创建等）。",
        "parameters": {
            "type": "object",
            "properties": {
                "history_file": {"type": "string", "description": "意图历史记录文件路径，默认 .forge/last_intent.json"},
            },
            "required": [],
        },
    },
    {
        "name": "git_log",
        "description": "查看最近 N 条 git 提交历史。",
        "parameters": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "返回的提交条数，默认 10"},
            },
            "required": [],
        },
    },
    {
        "name": "run_single_test",
        "description": "运行单个 pytest 文件或测试节点。比 run_command('python3 -m pytest ...') 更简洁。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "测试文件路径，如 tests/test_xxx.py 或 tests/test_xxx.py::test_func"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 60"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "run_command",
        "description": "在项目根目录下执行 shell 命令（跑测试、编译、grep 等）。",
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "要执行的 shell 命令"},
                "timeout": {"type": "integer", "description": "超时秒数，默认60"},
            },
            "required": ["cmd"],
        },
    },
    {
        "name": "read_function",
        "description": "只读取指定函数/类的源码（按符号名，基于符号索引）。比 read_file 更省 token。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "symbol_name": {"type": "string", "description": "函数或类名"},
            },
            "required": ["path", "symbol_name"],
        },
    },
    {
        "name": "run_type_check",
        "description": "类型检查：优先 mypy/pyright；不可用时做 AST 注解启发式检查。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件或目录，默认 ."},
                "tool": {"type": "string", "description": "auto|mypy|pyright|ast，默认 auto"},
            },
            "required": [],
        },
    },
    {
        "name": "resolve_path_object",
        "description": "文件路径 → Veritas ObjectId。修改文件前可用此工具确认 ID。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对项目根的文件路径"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "rebuild_symbol_index",
        "description": "强制重建全仓符号索引缓存（.forge/symbols.json）。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]

# Mutation tools for Runtime tool-loop (World + file).
# World ops do not write files; file ops project to disk after Veritas commit.
MUTATION_TOOL_DECLARATIONS = [
    {
        "name": "create_object",
        "description": (
            "World 操作：在 Veritas 世界中创建一个纯世界对象（不创建文件、不写磁盘）。"
            "成功后返回 ObjectId（整数）。"
            "若任务是「创建对象 / link 对象 / World 对象」，必须用本工具，禁止用 create_file。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "create_file",
        "description": (
            "文件操作：创建仓库中的新文件并投影到磁盘。"
            "仅当用户明确要求创建文件/路径/代码文件时使用；"
            "不要用本工具代替 create_object。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径（相对于项目根目录）"},
                "content": {"type": "string", "description": "文件内容，默认为空"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "modify_file",
        "description": (
            "文件操作：修改已有文件。operations 可含多处修改。"
            "object_id 可选——省略时按 path 自动解析。"
            "不是 World 纯对象操作。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "object_id": {
                    "type": "integer",
                    "description": "可选。省略则按 path 查 ObjectPathMap",
                },
                "operations": {
                    "type": "array",
                    "description": "修改操作列表（可多处）。machine: start_line/end_line + new_lines",
                },
            },
            "required": ["path", "operations"],
        },
    },
    {
        "name": "edit_files_batch",
        "description": (
            "文件操作：在同一 Veritas 事务中批量修改多个文件。"
            "edits: [{path, operations, object_id?}, ...]。"
            "比多次 modify_file 更高效。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "description": "[{path, operations, object_id?}, ...]",
                },
            },
            "required": ["edits"],
        },
    },
    {
        "name": "delete_file",
        "description": (
            "文件操作：删除文件对应的 Veritas Object（可附带 path 供投影删盘）。"
            "不是 unlink_objects；删除的是文件对象本身。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "object_id": {"type": "integer", "description": "要删除的 Object ID"},
                "path": {"type": "string", "description": "可选，文件路径，用于投影删除"},
            },
            "required": ["object_id"],
        },
    },
    {
        "name": "link_objects",
        "description": (
            "World 操作：在两个已存在的 ObjectId 之间建立 Link。"
            "from_id / to_id 必须来自 create_object 返回或 list_world_objects，禁止编造。"
            "link_type: owns / depends_on / references（默认 owns）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "from_id": {"type": "integer", "description": "源 Object ID"},
                "to_id": {"type": "integer", "description": "目标 Object ID"},
                "link_type": {
                    "type": "string",
                    "description": "Link 类型: owns/depends_on/references，默认 owns",
                },
            },
            "required": ["from_id", "to_id"],
        },
    },
    {
        "name": "unlink_objects",
        "description": (
            "World 操作：删除两个 Object 之间的 Link。"
            "from_id / to_id 必须是真实 ObjectId，禁止编造。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "from_id": {"type": "integer", "description": "源 Object ID"},
                "to_id": {"type": "integer", "description": "目标 Object ID"},
            },
            "required": ["from_id", "to_id"],
        },
    },
]

# Default LLM-visible set remains read-only for legacy imports.
# Production Runtime._run_conversation uses READ_ONLY + MUTATION.
TOOL_DECLARATIONS = list(READ_ONLY_TOOL_DECLARATIONS)

MUTATION_TOOL_NAMES = frozenset(
    d["name"] for d in MUTATION_TOOL_DECLARATIONS
)
