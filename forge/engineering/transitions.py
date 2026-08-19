"""
DEPRECATED: 六阶段 engineering 已不作为生产入口；生产 = Runtime 工具循环。本文件仅供历史测试。
Engineering Loop 状态转移规则"""
from forge.engineering.phases import Phase

# 正常流转
TRANSITIONS = {
    Phase.UNDERSTAND: Phase.PLAN,
    Phase.PLAN: Phase.REVIEW,
    Phase.REVIEW: Phase.EXECUTE,
    Phase.EXECUTE: Phase.VERIFY,
    Phase.VERIFY: Phase.COMPLETE,
}

# 失败回跳
RETRY_TRANSITIONS = {
    Phase.REVIEW: Phase.PLAN,     # 宪法不通过 → 重新规划
    Phase.EXECUTE: Phase.EXECUTE, # 执行失败 → 重试（最多3次）
    Phase.VERIFY: Phase.EXECUTE,  # 验证失败 → 重新执行
}

MAX_RETRIES = 3


def next_phase(current: Phase, success: bool, retry_count: int = 0) -> Phase:
    """计算下一个阶段"""
    if success:
        return TRANSITIONS.get(current, Phase.COMPLETE)
    else:
        if retry_count >= MAX_RETRIES:
            return Phase.FAILED
        return RETRY_TRANSITIONS.get(current, Phase.FAILED)
