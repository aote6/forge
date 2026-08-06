"""Intent layer — translates LLM semantic intent into Veritas primitive sequences.

LLM 永远只描述 What，Intent 层负责展开成 How。
"""

from forge.intents.intent import Intent, IntentType
from forge.intents.executor import IntentExecutor

__all__ = ["Intent", "IntentType", "IntentExecutor"]
