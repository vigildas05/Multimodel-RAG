import base64
import logging

import httpx

from app.core.exceptions import LLMProviderError
from app.services.llm.base import LLMGenerationResult, LLMProvider

logger = logging.getLogger("app.services.llm.ollama")


class OllamaProvider(LLMProvider):
    provider_name = "ollama"

    def __init__(self, base_url: str, text_model: str, vision_model: str, timeout: float = 120.0):
        self._base_url = base_url.rstrip("/")
        self._text_model = text_model
        self.model_name = text_model
        self._vision_model = vision_model
        self._timeout = timeout

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        top_p: float,
        max_output_tokens: int,
    ) -> LLMGenerationResult:
        payload = {
            "model": self._text_model,
            "prompt": user_prompt,
            "system": system_prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_predict": max_output_tokens,
            },
        }
        data = await self._post("/api/generate", payload)
        return LLMGenerationResult(
            text=data.get("response", ""),
            finish_reason=data.get("done_reason"),
            raw_usage={
                "prompt_eval_count": data.get("prompt_eval_count"),
                "eval_count": data.get("eval_count"),
            },
        )

    async def describe_image(
        self,
        image_bytes: bytes,
        prompt: str,
        temperature: float,
        max_output_tokens: int,
    ) -> str:
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        payload = {
            "model": self._vision_model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_output_tokens,
            },
        }
        data = await self._post("/api/generate", payload)
        return data.get("response", "")

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def _post(self, path: str, payload: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base_url}{path}", json=payload)
                resp.raise_for_status()
                return resp.json()
        except httpx.ConnectError as exc:
            raise LLMProviderError(
                "Could not connect to Ollama.",
                detail=f"Is Ollama running at {self._base_url}? ({exc})",
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LLMProviderError(
                f"Ollama returned an error (status {exc.response.status_code}).",
                detail=exc.response.text,
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError("Ollama request failed.", detail=str(exc)) from exc
