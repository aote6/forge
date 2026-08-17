# Planner 决策权边界 - Machine vs LLM

日期：2026-08-17
状态：正式架构原则，跨项目通用

## 背景

历史上已经三次在机器与 LLM 的决策权边界上踩坑：

第一次：2026-08-06 PlanRepair 自动修正 LLM 的 old_text
错误：机器替 AI 猜意图
教训：删除 PlanRepair

第二次：2026-08-09 dp.py 入口无读写分流
错误：所有输入走修改链路
教训：机器粗分类可以

第三次：2026-08-17 Planner 把创建对象误判为修改代码
错误：机器没有拒绝机制
教训：Validator fail-closed

## 核心原则

机器可以进行有限的、显式定义边界的粗粒度入口路由。
机器不得在 LLM 已完成细粒度语义决策后，以猜测方式修改其决策。

## 允许的机器行为

入口粗分类：读写二分，例如 dp.py 的 is_engineering_task
客观事实提取：从仓库或世界读取真实状态，例如 RepositoryIndex 推导 impact_files
能力存在性标注：符号已存在且已验证可用，例如 tx_create_object 是已闭环能力
契约裁决：拒绝非法组合，不修改，例如 Validator 拒绝 create_object 加 target_files

## 禁止的机器行为

事后纠正 LLM 的 operation_type：机器替 AI 猜意图，例如 LLM 说 modify，机器改成 create_object
自动补全缺失字段：机器变成隐形 Planner，例如自动把 target_files 从空改成 adapter.py
模糊匹配猜测 old_text：8月6日已证伪，例如 PlanRepair
用关键词覆盖 LLM 的细粒度决策：机器没有足够语义理解，例如看到 create 就强制 create_object

## 决策流程

用户任务
进入事实增强层（机器）
标注已有能力：tx_create_object 等于 VERIFIED_AVAILABLE
推导 impact_files 和 impact_symbols
提供 operation candidates（world-op 或 code-op）
进入 LLM 两阶段决策
第一阶段：WORLD_OPERATION 还是 CODE_OPERATION
WORLD_OPERATION 对应 create_object 或 link_objects
CODE_OPERATION 对应 modify 或 create_file 或 delete_file
第二阶段：具体 operation_type 加必要参数
进入 Validator fail-closed
合法则通过
非法则 REJECT(reason)，不修改，返回给 Planner retry
LLM 根据 rejection reason 重新决策

## Validator 契约

必须拒绝的情况：
create_object 加 target_files 非空，拒绝，不清空 target_files
modify 加 target_files 为空，拒绝，不补全
任务请求调用已有能力，计划提出修改该能力，且无修改理由，拒绝

不得修正：
Validator 是纯函数式裁决
输入产生 VALID 或 REJECT(reason)
不是输入后偷偷修改再 VALID

## 已有能力保护

当任务引用一个已存在且已验证可用的能力时：
计划是调用该能力（如 create_object），通过
计划是修改该能力且无理由，拒绝
计划是修改该能力且有明确理由（如修复 bug），通过

调用已有能力不等于修改已有能力。

## 历史案例统一

dp.py 读写分流：入口粗分类，允许
RepositoryIndex 推导 impact：事实提取，允许
tx_create_object 标注为已存在：事实标注，允许
LLM 选择 create_object：LLM 决策，允许
Validator 拒绝非法组合：契约裁决，允许
Validator 把 modify 改成 create_object：猜意图，禁止
PlanRepair 猜 old_text：猜意图，禁止
