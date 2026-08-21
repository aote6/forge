"""OpenRouter 适配器（免费模型用 :free 后缀）"""
from forge.adapters.openai_compat import OpenAICompatAdapter


class OpenRouterAdapter(OpenAICompatAdapter):
    def __init__(self, model_name: str = "nvidia/nemotron-3-ultra-550b-a55b:free"):
        # OpenRouter 建议带 HTTP-Referer 和 X-Title，方便统计
        super().__init__(
            model_name=model_name,
            api_key_env="OPENROUTER_API_KEY",
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://github.com/aote6/forge",
                "X-Title": "Forge",
            },
        )
