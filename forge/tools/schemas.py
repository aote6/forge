"""工具声明 — LLM 可见的工具集。

READ_ONLY_TOOL_DECLARATIONS: conversation / legacy 唯一允许集合。
MUTATION_TOOL_DECLARATIONS: 仅供文档/测试对照；生产 mutation 必须走
EngineeringOrchestrator → ExecutionAdapter → IntentExecutor，不得经 tool-loop。

TOOL_DECLARATIONS 保持为只读集合（兼容旧 import），避免误把 mutation 暴露给 LLM。
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
]

# Historical mutation tool schemas — NOT registered on conversation/legacy paths.
# Mutations must go through EngineeringOrchestrator only.
MUTATION_TOOL_DECLARATIONS = [
    {
        "name": "create_file",
        "description": "创建一个新文件。仅可通过 EngineeringOrchestrator 执行，不可在 tool-loop 中调用。",
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
        "description": "修改已有文件。仅可通过 EngineeringOrchestrator 执行。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "object_id": {"type": "integer", "description": "文件的 Veritas Object ID"},
                "operations": {"type": "array", "description": "修改操作列表"},
            },
            "required": ["path", "object_id", "operations"],
        },
    },
    {
        "name": "delete_file",
        "description": "删除文件对应的 Veritas Object。仅可通过 EngineeringOrchestrator 执行。",
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
        "description": "在两个 Object 之间建立 Link。仅可通过 EngineeringOrchestrator 执行。",
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
        "description": "删除两个 Object 之间的 Link。仅可通过 EngineeringOrchestrator 执行。",
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

# Default LLM-visible set: read/discovery only (P1-A closure).
TOOL_DECLARATIONS = list(READ_ONLY_TOOL_DECLARATIONS)

MUTATION_TOOL_NAMES = frozenset(
    d["name"] for d in MUTATION_TOOL_DECLARATIONS
)
