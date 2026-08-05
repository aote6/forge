"""工具声明（Gemini 格式）"""

TOOL_DECLARATIONS = [
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
            "required": ["path"]
        }
    },
    {
        "name": "prepare_write",
        "description": (
            "准备修改文件。operations 是操作列表，每个操作包含：\n"
            "- type: 'replace' | 'insert_before' | 'insert_after' | 'delete'\n"
            "- anchor: 定位锚点字符串（如函数名、类名）\n"
            "- target: 要操作的代码片段\n"
            "- value: 新代码片段（delete 时不需要）\n"
            "返回事务 ID 和 diff 预览，需要用户确认后调用 commit_write。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要修改的文件路径"},
                "operations": {
                    "type": "array",
                    "description": "操作列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "description": "操作类型"},
                            "anchor": {"type": "string", "description": "定位锚点"},
                            "target": {"type": "string", "description": "要操作的代码"},
                            "value": {"type": "string", "description": "新代码"},
                        },
                        "required": ["type", "anchor"]
                    }
                },
            },
            "required": ["path", "operations"]
        }
    },
    {
        "name": "commit_write",
        "description": "提交之前 prepare_write 创建的事务。需要传入事务 ID。",
        "parameters": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "string", "description": "事务 ID"},
            },
            "required": ["transaction_id"]
        }
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
            "required": ["pattern"]
        }
    },
    {
        "name": "cancel_write",
        "description": "取消之前 prepare_write 创建但尚未提交的事务。",
        "parameters": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "string", "description": "事务 ID"},
            },
            "required": ["transaction_id"]
        }
    },
    {
        "name": "run_command",
        "description": "在项目根目录下执行 shell 命令（跑测试、编译、grep 等）。立即执行，不需要用户确认。谨慎使用有破坏性的命令。",
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "要执行的 shell 命令"},
                "timeout": {"type": "integer", "description": "超时秒数，默认60"},
            },
            "required": ["cmd"]
        }
    },
]
