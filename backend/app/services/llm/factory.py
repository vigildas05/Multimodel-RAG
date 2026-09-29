import threading

from app.config import Settings
from app.core.exceptions import ConfigurationError
from app.services.llm.base import LLMProvider
from app.services.llm.ollama_provider import OllamaProvider

_instance: LLMProvider | None = None
_lock = threading.Lock()


def get_llm_provider(settings: Settings) -> LLMProvider:
    global _instance
    if _instance is not None:
        return _instance
    with _lock:
        if _instance is not None:
            return _instance
        provider_name = settings.LLM_PROVIDER.lower()
        if provider_name == "ollama":
            _instance = OllamaProvider(
                base_url=settings.OLLAMA_BASE_URL,
                text_model=settings.OLLAMA_TEXT_MODEL,
                vision_model=settings.OLLAMA_VISION_MODEL,
                timeout=settings.OLLAMA_REQUEST_TIMEOUT,
            )
        elif provider_name == "groq":
            from app.services.llm.groq_provider import GroqProvider
            import os

            _instance = GroqProvider(api_key=os.environ.get("GROQ_API_KEY", ""))
        else:
            raise ConfigurationError(f"Unknown LLM_PROVIDER '{settings.LLM_PROVIDER}'.")
        return _instance


def reset_provider_for_testing() -> None:
    global _instance
    _instance = None
