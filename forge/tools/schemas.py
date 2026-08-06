"""工具声明"""

TOOL_DECLARATIONS = [
    {
        "name": "world_whoami",
        "description": "返回 Forge 在 Veritas 世界中的 ObjectId（系统软件身份）。",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "world_info",
        "description": "查询世界版本、state_root、Object 数量。",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "world_list_objects",
        "description": "列出 Veritas 世界中所有 Object（ID 和状态）。",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "world_get_object",
        "description": "查询指定 Object 的状态（Alive/Frozen/Dead）。",
        "parameters": {
            "type": "object",
            "properties": {"object_id": {"type": "integer", "description": "Object ID"}},
            "required": ["object_id"]
        }
    },
    {
        "name": "world_get_links",
        "description": "列出世界中的 Link 关系。",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "world_begin",
        "description": "开始一个世界事务 Session（可包含多个 mutation，最后 world_commit）。",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "world_create_object",
        "description": "在当前世界 Session 中创建 Object（需先 world_begin，提交后生效）。",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "world_freeze",
        "description": "在当前 Session 中冻结 Object。",
        "parameters": {
            "type": "object",
            "properties": {"object_id": {"type": "integer"}},
            "required": ["object_id"]
        }
    },
    {
        "name": "world_death",
        "description": "在当前 Session 中销毁 Object。",
        "parameters": {
            "type": "object",
            "properties": {"object_id": {"type": "integer"}},
            "required": ["object_id"]
        }
    },
    {
        "name": "world_link",
        "description": "在当前 Session 中建立 Link（link_type: owns/depends_on/references）。",
        "parameters": {
            "type": "object",
            "properties": {
                "from_id": {"type": "integer"},
                "to_id": {"type": "integer"},
                "link_type": {"type": "string", "description": "owns|depends_on|references"}
            },
            "required": ["from_id", "to_id"]
        }
    },
    {
        "name": "world_unlink",
        "description": "在当前 Session 中删除 Link。",
        "parameters": {
            "type": "object",
            "properties": {
                "from_id": {"type": "integer"},
                "to_id": {"type": "integer"}
            },
            "required": ["from_id", "to_id"]
        }
    },
    {
        "name": "world_commit",
        "description": "提交当前世界 Session，返回 Transaction Receipt（tx_id/before_root/after_root/version）。",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "world_abort",
        "description": "中止当前世界 Session，丢弃未提交变更。",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "list_files",
        "description": "列出项目目录结构，用于了解项目布局。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径，默认项目根目录"},
                "depth": {"type": "integer", "description": "深度，默认2"},
            },
            "required": []
        }
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
            "required": ["path"]
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
        "name": "git_diff",
        "description": "查看当前工作区未暂存的修改。",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "name": "prepare_write",
        "description": "[DEPRECATED] 请使用 create_file 或 modify_file。准备修改文件。operations 使用 anchor 定位。返回事务 ID 和 diff 预览，需要用户确认后调用 commit_write。",
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
        "description": "[DEPRECATED] 请使用 create_file 或 modify_file，它们会自动提交。提交之前 prepare_write 创建的事务。需要传入事务 ID。",
        "parameters": {
            "type": "object",
            "properties": {
                "transaction_id": {"type": "string", "description": "事务 ID"},
            },
            "required": ["transaction_id"]
        }
    },
    {
        "name": "cancel_write",
        "description": "[DEPRECATED] 请使用 world_abort。取消之前 prepare_write 创建但尚未提交的事务。",
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
        "description": "在项目根目录下执行 shell 命令（跑测试、编译、grep 等）。",
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "要执行的 shell 命令"},
                "timeout": {"type": "integer", "description": "超时秒数，默认60"},
            },
            "required": ["cmd"]
        }
        },
    {
        "name": "create_file",
        "description": "[PREFERRED] 创建一个新文件。指定路径和内容。这是推荐的文件创建方式。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径（相对于项目根目录）"},
                "content": {"type": "string", "description": "文件内容，默认为空"},
            },
            "required": ["path"]
        }
    },
    {
        "name": "modify_file",
        "description": "[PREFERRED] 修改已有文件。需要提供 object_id。operations 使用 anchor 定位修改位置。这是推荐的文件修改方式。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "object_id": {"type": "integer", "description": "文件的 Veritas Object ID"},
                "operations": {"type": "array", "description": "修改操作列表"}
            },
            "required": ["path", "object_id", "operations"]
        }
    },
    {
        "name": "delete_file",
        "description": "[PREFERRED] 删除文件对应的 Veritas Object。这是推荐的文件删除方式。",
        "parameters": {
            "type": "object",
            "properties": {
                "object_id": {"type": "integer", "description": "要删除的 Object ID"}
            },
            "required": ["object_id"]
        }
    },
    {
        "name": "link_objects",
        "description": "[PREFERRED] 在两个 Object 之间建立 Link 关系。这是推荐的链接方式。",
        "parameters": {
            "type": "object",
            "properties": {
                "from_id": {"type": "integer", "description": "源 Object ID"},
                "to_id": {"type": "integer", "description": "目标 Object ID"},
                "link_type": {"type": "string", "description": "Link 类型: owns/depends_on/references，默认 owns"}
            },
            "required": ["from_id", "to_id"]
        }
    },
    {
        "name": "unlink_objects",
        "description": "[PREFERRED] 删除两个 Object 之间的 Link。这是推荐的取消链接方式。",
        "parameters": {
            "type": "object",
            "properties": {
                "from_id": {"type": "integer", "description": "源 Object ID"},
                "to_id": {"type": "integer", "description": "目标 Object ID"}
            },
            "required": ["from_id", "to_id"]
        }
    },
]
