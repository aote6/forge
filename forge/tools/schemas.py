"""工具声明 — LLM 可见的精简工具集（质量优先，数量收敛）。

设计原则（对齐 Claude Code / Cursor Agent）:
  - 核心编辑: str_replace / write_file（文本级，高成功率）
  - 探索: read_file / glob_files / search_code / find_symbol_definition
  - 验证: run_command / run_test_structured / run_type_check / git_diff
  - World: create_object / link_objects + 查询
  - 高级/兼容: modify_file / edit_files_batch（多处行级编辑）

完整可调用实现仍由 make_tools 注册；本文件只决定 LLM 看到哪些 schema。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Read / explore — kept minimal
# ---------------------------------------------------------------------------
READ_ONLY_TOOL_DECLARATIONS = [
    {
        "name": "read_file",
        "description": "读取文件。大文件无行范围时返回函数/类大纲；指定 start/end 读片段；小文件返回全文。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "start": {"type": "integer", "description": "起始行，默认 1"},
                "end": {"type": "integer", "description": "结束行，0=全部"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "read_function",
        "description": "只读某个函数/类的源码（省 token）。优先于整文件 read_file。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "symbol_name": {"type": "string", "description": "函数或类名"},
            },
            "required": ["path", "symbol_name"],
        },
    },
    {
        "name": "glob_files",
        "description": "按 glob 模式列文件，如 **/*.py、forge/tools/*.py。比 list_files 更精确。",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式，相对项目根"},
                "max_results": {"type": "integer", "description": "最多返回条数，默认 200"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "search_code",
        "description": "在项目中正则/文本搜索代码，返回文件与行号。",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "搜索子路径，默认 ."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "find_symbol_definition",
        "description": "查符号定义（全仓索引 .forge/symbols.json）：类/函数 → 文件+行号。",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol_name": {"type": "string"},
            },
            "required": ["symbol_name"],
        },
    },
    {
        "name": "get_repo_map",
        "description": "项目结构与关键符号的压缩地图，用于快速建立全局印象。",
        "parameters": {
            "type": "object",
            "properties": {
                "root_dir": {"type": "string"},
                "max_tokens": {"type": "integer"},
            },
            "required": [],
        },
    },
    {
        "name": "git_diff",
        "description": "查看工作区未提交 diff。改完代码后应调用以确认变更。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "run_command",
        "description": "在项目根执行 shell（测试、构建、脚本）。危险命令会被拦截。",
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string"},
                "timeout": {"type": "integer", "description": "超时秒，默认 60"},
            },
            "required": ["cmd"],
        },
    },
    {
        "name": "run_test_structured",
        "description": "跑 pytest；失败时附带失败行前后源码上下文。",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "默认 tests/"},
            },
            "required": [],
        },
    },
    {
        "name": "run_type_check",
        "description": "类型检查：mypy/pyright（若可用）或 AST 注解启发式。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "tool": {"type": "string", "description": "auto|mypy|pyright|ast"},
            },
            "required": [],
        },
    },
    {
        "name": "world_info",
        "description": "Veritas 世界摘要：版本、对象数。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "list_world_objects",
        "description": "列出 World 中对象 id 与状态。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_world_object",
        "description": "查看指定 ObjectId 状态。",
        "parameters": {
            "type": "object",
            "properties": {
                "object_id": {"type": "integer"},
            },
            "required": ["object_id"],
        },
    },
    {
        "name": "list_world_links",
        "description": "列出对象间链接。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "search_history",
        "description": "搜索本会话对话日志（.forge/conversation_log.jsonl）。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "resolve_path_object",
        "description": "路径 → ObjectId。通常不必手动调用（str_replace/write_file 会自动解析与登记）。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "todo_write",
        "description": "写入/更新当前任务的待办列表。复杂任务（>3 步）应先拆解。items: [{id?, content, status}] status=pending|in_progress|done。",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "[{id?, content, status}]",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "content": {"type": "string"},
                            "status": {"type": "string"}
                        }
                    },
                },
            },
            "required": ["items"],
        },
    },
    {
        "name": "todo_list",
        "description": "查看当前内存中的待办列表。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "spawn_subagent",
        "description": (
            "派生子 Agent 处理复杂探索/定位子任务。"
            "子 Agent 有独立上下文与工具循环（只读+str_replace/write_file），"
            "最多 15 步；只把最终结论返回给主 Agent，避免搜索过程污染上下文。"
            "适合：跨多文件找 bug、梳理调用链、做小范围修复。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "给子 Agent 的明确子任务描述"},
                "max_steps": {"type": "integer", "description": "默认 15，上限 15"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "session_changes",
        "description": "列出本会话已成功修改的文件（path/tx/summary）。用户问改了哪些文件时使用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "project_memory",
        "description": "查看项目记忆：测试命令、最近改过的文件、曾失败测试、上次任务。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "web_fetch",
        "description": "抓取 http(s) URL 的文本内容（无 JS，超时 10s，默认截断 5000 字）。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {"type": "integer", "description": "默认 5000"},
            },
            "required": ["url"],
        },
    },

    {
        "name": "post_toot",
        "description": (
            "发一条 Mastodon 嘟文（可选，非强制）。"
            "需环境变量 MASTODON_BASE_URL + MASTODON_ACCESS_TOKEN。"
            "visibility: public|unlisted|private|direct，默认 unlisted。"
            "勿刷屏；勿在嘟文中包含密钥/token。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "嘟文正文"},
                "visibility": {
                    "type": "string",
                    "description": "public|unlisted|private|direct，默认 unlisted",
                },
            },
            "required": ["text"],
        },
    },
]

# ---------------------------------------------------------------------------
# Mutations — text-first edit primitives + World + batch
# ---------------------------------------------------------------------------
MUTATION_TOOL_DECLARATIONS = [
    {
        "name": "undo_last_tx",
        "description": "撤销最近一次成功的文件修改（shadow/事务）。改错了优先调用，不要盲猜第二次 str_replace。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "str_replace",
        "description": (
            "【首选编辑】在文件中做精确字符串替换（old_string → new_string）。"
            "old_string 必须在文件中唯一出现（或设 replace_all=true）。"
            "比按行号 modify_file 更稳，对齐 Claude Code Edit。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string", "description": "必须与文件中原文完全一致"},
                "new_string": {"type": "string"},
                "replace_all": {
                    "type": "boolean",
                    "description": "是否替换全部匹配，默认 false",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "整文件写入：创建新文件或覆盖已有文件全部内容。"
            "大块生成/重构时用本工具；小改动优先 str_replace。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string", "description": "完整文件内容"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "create_file",
        "description": "创建新文件（若已存在请用 write_file 或 str_replace）。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "modify_file",
        "description": (
            "按行级 operations 修改（高级）。日常编辑优先 str_replace。"
            "object_id 可省略（按 path 自动解析）。operations 可含多处。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "object_id": {"type": "integer"},
                "operations": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["path", "operations"],
        },
    },
    {
        "name": "edit_files_batch",
        "description": "同一 Veritas 事务中批量 modify 多文件。edits: [{path, operations, object_id?}, ...]",
        "parameters": {
            "type": "object",
            "properties": {
                "edits": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["edits"],
        },
    },
    {
        "name": "apply_patch",
        "description": (
            "应用 unified diff（git diff 格式）到一个或多个文件，单事务提交。"
            "支持 --- a/path +++ b/path @@ 块。多文件重构优先本工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patch": {"type": "string", "description": "完整 unified diff 文本"},
            },
            "required": ["patch"],
        },
    },
    {
        "name": "delete_file",
        "description": "删除文件对象。可只给 path（自动解析 ObjectId）或只给 object_id。",
        "parameters": {
            "type": "object",
            "properties": {
                "object_id": {"type": "integer"},
                "path": {"type": "string"},
            },
            "required": [],
        },
    },
    {
        "name": "create_object",
        "description": "World：创建纯世界对象（不写磁盘），返回 ObjectId。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "link_objects",
        "description": "World：链接两个 ObjectId。link_type: owns|depends_on|references。",
        "parameters": {
            "type": "object",
            "properties": {
                "from_id": {"type": "integer"},
                "to_id": {"type": "integer"},
                "link_type": {"type": "string"},
            },
            "required": ["from_id", "to_id"],
        },
    },
    {
        "name": "unlink_objects",
        "description": "World：删除两个对象之间的链接。",
        "parameters": {
            "type": "object",
            "properties": {
                "from_id": {"type": "integer"},
                "to_id": {"type": "integer"},
            },
            "required": ["from_id", "to_id"],
        },
    },
    {
        "name": "forge_sync",
        "description": (
            "显式同步 World ↔ Disk/Git：IN_SYNC 无操作；FAST_FORWARD 沿明确方向安全推进；"
            "CONFLICT 停止并展示 diff 等待用户决定。检测到外部修改后应优先调用本工具对账。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]

TOOL_DECLARATIONS = list(READ_ONLY_TOOL_DECLARATIONS)

MUTATION_TOOL_NAMES = frozenset(d["name"] for d in MUTATION_TOOL_DECLARATIONS)
