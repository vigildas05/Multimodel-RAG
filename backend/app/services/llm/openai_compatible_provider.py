"""Any OpenAI-compatible chat-completions API: Groq, Google Gemini (OpenAI endpoint),
NVIDIA NIM, OpenAI, Together, vLLM... selected by base URL + model in env.

    LLM_PROVIDER=openai_compatible
    LLM_BASE_URL=https://api.groq.com/openai/v1
    LLM_MODEL=llama-3.3-70b-versatile
    LLM_API_KEY=...                       # never logged or returned to clients
    LLM_VISION_MODEL=...                  # optional; without it images get OCR only
"""

import base64
import logging

import httpx

from app.core.exceptions import ConfigurationError, LLMProviderError
from app.services.llm.base import LLMGenerationResult, LLMProvider

logger = logging.getLogger("app.services.llm.openai_compatible")


class OpenAICompatibleProvider(LLMProvider):
    provider_name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        vision_model: str | None = None,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not base_url or not model:
            raise ConfigurationError("LLM_BASE_URL and LLM_MODEL are required for the openai_compatible provider.")
        if not api_key:
            raise ConfigurationError("LLM_API_KEY is required for the openai_compatible provider.")
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self.model_name = model
        self._vision_model = vision_model or None
        self._timeout = timeout
        self._transport = transport  # tests inject a mock transport

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        top_p: float,
        max_output_tokens: int,
    ) -> LLMGenerationResult:
        data = await self._chat(
            {
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "top_p": top_p,
                "max_tokens": max_output_tokens,
            }
        )
        choice = (data.get("choices") or [{}])[0]
        return LLMGenerationResult(
            text=(choice.get("message") or {}).get("content") or "",
            finish_reason=choice.get("finish_reason"),
            raw_usage=data.get("usage"),
        )

    async def describe_image(self, image_bytes: bytes, prompt: str, temperature: float, max_output_tokens: int) -> str:
        if not self._vision_model:
            logger.info("No LLM_VISION_MODEL configured; skipping image description (OCR text is still used).")
            return ""
        data_uri = "data:image/png;base64," + base64.b64encode(image_bytes).decode()
        data = await self._chat(
            {
                "model": self._vision_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_uri}},
                        ],
                    }
                ],
                "temperature": temperature,
                "max_tokens": max_output_tokens,
            }
        )
        return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""

    async def health_check(self) -> bool:
        try:
            async with self._client(timeout=5) as client:
                return (await client.get("/models")).status_code == 200
        except httpx.HTTPError:
            return False

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url, headers=self._headers, timeout=timeout, transport=self._transport
        )

    async def _chat(self, payload: dict) -> dict:
        try:
            async with self._client(timeout=self._timeout) as client:
                resp = await client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise LLMProviderError("The language model took too long to respond.") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError("Could not reach the language model provider.", detail=type(exc).__name__) from exc

        if resp.status_code == 200:
            return resp.json()
        # Provider error bodies can echo request details; keep only a short snippet for the logs.
        snippet = resp.text[:200]
        if resp.status_code in (401, 403):
            raise LLMProviderError("The language model provider rejected the API key.", detail=snippet)
        if resp.status_code == 429:
            raise LLMProviderError("The language model provider is rate-limiting requests. Try again shortly.", detail=snippet)
        raise LLMProviderError(f"The language model provider returned an error ({resp.status_code}).", detail=snippet)
