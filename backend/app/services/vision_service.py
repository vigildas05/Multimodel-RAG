"""Vision-LLM image description, used to make image content retrievable alongside
OCR text. Kept behind the same swappable LLMProvider interface as text generation.
"""

from app.core.exceptions import LLMProviderError
from app.services.llm.base import LLMProvider

VISION_DESCRIBE_PROMPT = (
    "Describe this image in detail for a document search system. Include any visible "
    "text, charts, diagrams, objects, and their relationships. Be factual and specific."
)


async def describe_image(provider: LLMProvider, image_bytes: bytes, temperature: float = 0.2) -> str:
    try:
        return await provider.describe_image(
            image_bytes=image_bytes,
            prompt=VISION_DESCRIBE_PROMPT,
            temperature=temperature,
            max_output_tokens=400,
        )
    except LLMProviderError:
        raise
