# Forge 项目状态

## 定位

Forge 是运行在 Veritas 世界上的工程 Agent。LLM 通过工具循环逐步执行任务，突变操作经 IntentExecutor -> WorldSession -> Veritas 事务 -> commit -> Projection。

依赖方向：Forge -> Veritas（经 WorldRuntime -> veritasd -> Kernel）。

## 生产 Runtime（唯一）

User Task
  -> Runtime.run(task)
    -> _run_conversation(task)  # 工具循环，唯一执行路径
      -> adapter.send(messages, READ_ONLY + MUTATION schemas)
        -> ToolExecutor.execute(tool_call)
          -> IntentExecutor -> WorldSession -> Veritas commit/abort
          -> ProjectionManager.project(...)

- Runtime.run 是唯一生产入口。所有任务走工具循环。
- Runtime.run_legacy 已废弃，仅保留兼容。
- 突变工具内部处理事务（begin -> execute -> commit/abort），LLM 不直接管理事务。
- World 操作（create_object / link_objects）和文件操作（create_file / modify_file / delete_file）在工具层硬分开。

## 工具集（32 个）

### World 操作（不写文件）

- create_object: 创建纯世界对象，返回 ObjectId
- link_objects: 建立对象链接（from_id, to_id, link_type）
- unlink_objects: 删除链接
- list_world_objects / list_world_links / world_info / get_world_object: 查询

### 文件操作（投影到磁盘）

- create_file / modify_file / delete_file

### 只读探索（26 个）

- list_files / read_file / read_files / read_file_with_lines
- search_code / get_repo_map / summarize_file / extract_code_skeleton
- find_symbol_definition / get_call_chain / get_symbol_line_range
- git_diff / git_log / git_status_enhanced / read_git_version / get_diff_summary
- run_command / run_single_test / run_test_structured / run_diagnostics
- get_context_budget / search_history / preview_line_mutation
- inspect_last_intent

## 已废弃模块（不删，测试仍引用）

- forge/orchestrator/engine.py — 旧六阶段 Orchestrator
- forge/planner.py — 旧 Planner（生成完整 Plan JSON）
- forge/plan_validator.py — 旧 Plan 结构校验
- forge/engineering/* — 旧六阶段状态机

## 已知限制

- veritasd 需在 PATH 或默认路径可用
- 单活跃 Session（Runtime 级）
- LLM 工具循环最多 20 轮
- 工具数量多，LLM 需要清晰的 system prompt 引导（已提供决策树格式）

## 测试状态

- 333 passed, 1 xfailed
- test_tool_loop_create_object_wiring.py: 验证 create_object 工具接线
