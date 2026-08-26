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
        "description": (
            "仅搜索本会话/对话历史日志（.forge/conversation_log.jsonl）。"
            "不是项目工作历史。默认不要用本工具回答「今天做了什么/最近完成什么」；"
            "项目回顾请用 project_review。"
        ),
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
        "name": "verify_tool_call",
        "description": (
            "按 tool_call_id 独立反查 ToolCallRecord（只读）。"
            "返回 tool_name/input/output/status/error/subtask_id；"
            "不返回子 Agent 的 claim 或 conclusion。"
            "验收 spawn_subagent 的 done 候选时必须对 evidence 中的 id 调用本工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tool_call_id": {
                    "type": "string",
                    "description": "Evidence 中的 tool_call_id（如 tc_…）",
                },
            },
            "required": ["tool_call_id"],
        },
    },
    {
        "name": "session_changes",
        "description": (
            "当前 session 的 mutation evidence（path/tx/summary）。"
            "session ≠ calendar day；不能替代 Git 的日历工作历史。"
            "用户问「本会话改了哪些文件」时用；问「今天/最近项目做了什么」用 project_review。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "project_memory",
        "description": (
            "启发式项目记忆（测试命令、recent_files、last_task 等），可能过期。"
            "不得作为项目当前事实或项目历史的权威来源。项目提交历史以 Git / project_review 为准。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "project_review",
        "description": (
            "【项目回顾统一入口】回答「今天/最近做了什么、项目状态、最近修改、测试是否通过、进度」时优先调用。"
            "返回 FACT(Git commits/worktree + 可验证 last_test_result) / EVIDENCE(STATUS.md 叙事, verified=false) / "
            "CONTEXT(project_memory 启发式; 可选 session/conversation) / CONFLICTS(不静默合并)。"
            "不生成 INFERENCE；不执行 pytest。默认 since=today、不包含 conversation。"
            "项目工作历史以 Git 为事实源；STATUS 不得升格为 Fact；无测试持久化结果时 tests.status=unverified。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "since": {
                    "type": "string",
                    "description": "起始日期 YYYY-MM-DD 或 today；默认本地日历今天",
                },
                "until": {
                    "type": "string",
                    "description": "结束日期 YYYY-MM-DD（可选，git --until）",
                },
                "include_status": {
                    "type": "boolean",
                    "description": "是否纳入 STATUS.md Evidence，默认 true",
                },
                "include_session": {
                    "type": "boolean",
                    "description": "是否纳入 session_changes Context，默认 false",
                },
                "include_conversation": {
                    "type": "boolean",
                    "description": "是否纳入对话历史 Context，默认 false",
                },
            },
            "required": [],
        },
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
]

# ---------------------------------------------------------------------------
# Mutations — text-first edit primitives + World + batch
# ---------------------------------------------------------------------------
MUTATION_TOOL_DECLARATIONS = [
    {
        "name": "post_toot",
        "description": (
            "已注册的 Mastodon 发帖工具：需要发嘟时直接调用本工具，无需搜索实现或手写脚本。"
            "参数 text 为嘟文正文；visibility 可选 public|unlisted|private|direct，默认 unlisted。"
            "依赖环境变量 MASTODON_BASE_URL 与 MASTODON_ACCESS_TOKEN。"
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
    {
        "name": "delete_toot",
        "description": (
            "删除指定 Mastodon 嘟文。"
            "需环境变量 MASTODON_BASE_URL + MASTODON_ACCESS_TOKEN。"
            "status_id 是要删除的嘟文 ID。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status_id": {"type": "string", "description": "要删除的嘟文 ID"},
            },
            "required": ["status_id"],
        },
    },
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
]

MUTATION_TOOL_NAMES = frozenset(d["name"] for d in MUTATION_TOOL_DECLARATIONS)

# ---------------------------------------------------------------------------
# Reconciliation — 恢复一致性工具声明（非权限 phase；Runtime 策略为 WRITE_RECOVERY）
# （forge_sync 契约：IN_SYNC 无操作 / FAST_FORWARD 安全推进 / CONFLICT 停止报告）
# ---------------------------------------------------------------------------
RECONCILIATION_TOOL_DECLARATIONS = [
    {
        "name": "forge_sync",
        "description": (
            "显式同步 World ↔ Disk/Git：IN_SYNC 无操作；FAST_FORWARD 沿明确方向安全推进；"
            "CONFLICT 停止并展示 diff 等待用户决定。检测到外部修改后应优先调用本工具对账。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]

RECONCILIATION_TOOL_NAMES = frozenset(
    d["name"] for d in RECONCILIATION_TOOL_DECLARATIONS
)

# ---------------------------------------------------------------------------
# 工具面分类（对齐 schemas.py ↔ make_tools 实际暴露面）
#
#   LLM/schema 可见 = READ_ONLY_TOOL_DECLARATIONS ∪ MUTATION_TOOL_DECLARATIONS
#                    （另有 submit_plan 规划阶段专用；spawn_subagent 由 Runtime
#                     注册，forge_sync 由 make_tools 在带 sync_layer 时注册）。
#   runtime/internal = INTERNAL_TOOL_NAMES —— 仍被 make_local_tools 注册为可调用
#                     实现（测试/兼容/内部辅助用），但刻意不出现在任何 schema 里，
#                     因此不会暴露给 Planner/LLM。不要为了\"面一致\"把它们升格为公开。
# ---------------------------------------------------------------------------
INTERNAL_TOOL_NAMES = frozenset({
    "read_file_with_lines",
    "preview_line_mutation",
    "get_symbol_line_range",
    "get_call_chain",
    "get_diff_summary",
    "extract_code_skeleton",
    "git_status_enhanced",
    "list_tests",
    "read_git_version",
    "summarize_file",
    "read_files",
    "run_diagnostics",
    "get_context_budget",
    "inspect_last_intent",
    "list_files",
    "git_log",
    "run_single_test",
    "rebuild_symbol_index",
})

# ---------------------------------------------------------------------------
# 可选方案工具 —— 复杂任务可先给出方案供讨论；不是写操作的必经前门。
# ---------------------------------------------------------------------------
SUBMIT_PLAN_TOOL_NAME = "submit_plan"
SUBMIT_PLAN_DECLARATION = {
    "name": SUBMIT_PLAN_TOOL_NAME,
    "description": (
        "【可选】复杂任务可先用本工具提交执行方案供用户讨论。"
        "不是写操作的必经前门：需要改文件/发嘟时可直接调用对应工具，"
        "Runtime 会在执行前要求确认精确动作。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plan": {
                "type": "string",
                "description": "执行计划：要改哪些文件、怎么改、为什么，以及如何验证。",
            },
        },
        "required": ["plan"],
    },
}
