# Forge 项目状态

## 定位

Forge 是运行在 Veritas 世界上的第一个系统软件（类似 BIOS / 第一层系统软件）。

依赖方向：Forge → Veritas（经 WorldRuntime → veritasd → Kernel）。

## 当前能力

- 宿主机工具：read_file / search_code / prepare_write / commit_write / cancel_write / run_command
- 世界工具：world_whoami / world_info / world_list_objects / world_get_object / world_get_links /
  world_begin / world_create_object / world_freeze / world_death / world_link / world_unlink /
  world_commit / world_abort
- WorldRuntime：Identity（.forge/world_identity）+ 长事务 Session + Receipt
- 双模型支持：gg（Gemini）/ dp（DeepSeek）

## 架构

```
LLM Tools (world_*)
  → WorldRuntime
    → WorldAdapter (JSON Lines)
      → veritasd
        → WorldService
          → Kernel::handle / commit
```

LLM 不得直接调用 VeritasClient 或 KernelCall。

## 已知限制

- veritasd 需在 PATH 或默认路径可用
- 单活跃 Session（Runtime 级）
- 本地文件事务与世界事务仍分离
