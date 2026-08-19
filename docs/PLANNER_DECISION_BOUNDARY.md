# Forge 决策权边界 — 工具循环模式

日期：2026-08-19
状态：正式架构原则

## 核心原则

LLM 通过工具循环逐步执行任务。每步工具调用返回真实结果，LLM 基于结果决定下一步。

机器不猜 LLM 意图。机器只提供工具和事实。

## 决策流程

用户任务
  -> LLM 选择工具（基于 system prompt 决策树）
    -> 工具执行（参数校验在工具内部）
      -> 返回结果（含 ObjectId / tx_id / version 等事实）
        -> LLM 看结果继续下一步
          -> 直到完成

## 工具内部校验

- create_object: 无需参数，返回 ObjectId
- link_objects: 校验 from_id/to_id 是 int，link_type 合法
- create_file: 校验 path 非空，content 非空
- modify_file: 校验 object_id 存在且 Alive

## 禁止的行为

- 禁止用 create_file 代替 create_object
- 禁止编造 ObjectId
- 禁止在 tool-loop 外执行突变

## 历史

旧架构（六阶段 Orchestrator + Planner + Validator）已废弃。
原因：LLM 在不知道执行结果的情况下被要求预测所有步骤参数，
导致 create-then-link 类任务无法完成。
