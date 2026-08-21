"""Groq 适配器（速度快）"""
from forge.adapters.openai_compat import OpenAICompatAdapter


class GroqAdapter(OpenAICompatAdapter):
    def __init__(self, model_name: str = "llama-3.3-70b-versatile"):
        super().__init__(
            model_name=model_name,
            api_key_env="GROQ_API_KEY",
            base_url="https://api.groq.com/openai/v1",
        )
