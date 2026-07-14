"""独立于 Conversation 的长期记忆存储"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryStore:
    facts: dict = field(default_factory=dict)
    preferences: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)
    
    def remember(self, key: str, value: Any):
        self.facts[key] = value
    
    def recall(self, key: str) -> Any:
        return self.facts.get(key)
    
    def set_preference(self, key: str, value: Any):
        self.preferences[key] = value
    
    def get_preference(self, key: str, default=None):
        return self.preferences.get(key, default)
    
    def clear(self):
        self.facts.clear()
        self.cache.clear()
