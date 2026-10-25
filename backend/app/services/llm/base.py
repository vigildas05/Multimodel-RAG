"""Abstract LLM provider interface.

Every generation/vision call in the app goes through this interface, never through a
provider SDK directly. Swapping providers (Ollama -> Groq -> OpenAI -> Anthropic) means
writing one new class here and registering it in factory.py — no changes to routers,
services, or prompt construction.

Note the naming: `temperature` / `top_p` here are LLM *sampling* parameters that shape
token generation. This is a completely different concept from retrieval's `top_k`
(the number of candidate chunks pulled from the vector store). Never call a sampling
parameter here `top_k` to avoid that exact confusion.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMGenerationResult:
    text: str
    finish_reason: str | None = None
    raw_usage: dict | None = None


class LLMProvider(ABC):
    # Recorded in query_logs; set by each implementation.
    provider_name: str = "unknown"
    model_name: str | None = None

    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        top_p: float,
        max_output_tokens: int,
    ) -> LLMGenerationResult:
        """Generate a grounded text response. Must raise LLMProviderError on failure."""

    @abstractmethod
    async def describe_image(
        self,
        image_bytes: bytes,
        prompt: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        """Vision-capable description/captioning, used for image ingestion and /images/analyze."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the provider is reachable and configured correctly."""
