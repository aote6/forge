"""
DEPRECATED: 六阶段 engineering 已不作为生产入口；生产 = Runtime 工具循环。本文件仅供历史测试。
Engineering Loop 阶段定义"""
from enum import Enum


class Phase(Enum):
    UNDERSTAND = "understand"      # 仓库感知
    PLAN = "plan"                  # 生成计划
    REVIEW = "review"              # 宪法审查
    EXECUTE = "execute"            # 执行修改
    VERIFY = "verify"              # 验证结果
    COMPLETE = "complete"          # 完成
    FAILED = "failed"              # 失败
