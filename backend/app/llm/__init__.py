from .base import LLMClient, LLMRequest, LLMUnavailableError
from .demo import DemoLLM
from .ollama import OllamaClient

from ..settings import AppSettings


def build_llm(s: AppSettings) -> LLMClient:
    """設定に従って LLM を作る。provider の自動切り替えはしない。"""
    if s.llm_provider == "ollama":
        return OllamaClient(s.ollama_url, s.ollama_model, s.ollama_timeout_sec)
    return DemoLLM()


__all__ = ["LLMClient", "LLMRequest", "LLMUnavailableError", "DemoLLM", "OllamaClient", "build_llm"]
