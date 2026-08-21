"""智谱 GLM 适配器（OpenAI 兼容）"""
from forge.adapters.openai_compat import OpenAICompatAdapter


class ZhipuAdapter(OpenAICompatAdapter):
    def __init__(self, model_name: str = "glm-4.7-flash"):
        # 智谱 OpenAI 兼容端点
        super().__init__(
            model_name=model_name,
            api_key_env="ZHIPU_API_KEY",
            base_url="https://open.bigmodel.cn/api/paas/v4",
        )
