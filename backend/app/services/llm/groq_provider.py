"""Stub provider demonstrating that swapping the LLM backend requires only a new
class conforming to LLMProvider + a factory registry entry — no changes anywhere
else in the app. Not wired up for the MVP (would need `groq` SDK + GROQ_API_KEY);
included to prove the abstraction and documented in the README as a future option.
"""

from app.core.exceptions import ConfigurationError
from app.services.llm.base import LLMGenerationResult, LLMProvider


class GroqProvider(LLMProvider):
    def __init__(self, api_key: str, text_model: str = "llama-3.3-70b-versatile"):
        if not api_key:
            raise ConfigurationError("GROQ_API_KEY is required to use the Groq provider.")
        self._api_key = api_key
        self._text_model = text_model

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        top_p: float,
        max_output_tokens: int,
    ) -> LLMGenerationResult:
        raise NotImplementedError(
            "GroqProvider is a scaffold only. Install the `groq` SDK and implement this "
            "method to enable it (see LLMProvider for the required interface)."
        )

    async def describe_image(
        self,
        image_bytes: bytes,
        prompt: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        raise NotImplementedError("GroqProvider vision support is not implemented in this MVP.")

    async def health_check(self) -> bool:
        return False
