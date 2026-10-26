"""OpenAI-compatible provider against a mocked HTTP transport (no network)."""

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.core.exceptions import ConfigurationError, LLMProviderError
from app.services.llm import factory
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider

KEY = "gsk_test_key_should_never_leak"


def provider(handler, **kwargs) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url="https://llm.example/v1/",
        api_key=KEY,
        model="test-model",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def run(coro):
    return asyncio.run(coro)


def test_generate_sends_openai_payload_and_parses_answer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Self-attention [1]."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 4},
            },
        )

    result = run(provider(handler).generate("SYS", "USER", temperature=0.2, top_p=0.9, max_output_tokens=100))
    assert result.text == "Self-attention [1]." and result.finish_reason == "stop"
    assert result.raw_usage == {"prompt_tokens": 50, "completion_tokens": 4}
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"]["model"] == "test-model" and seen["body"]["max_tokens"] == 100
    assert seen["body"]["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]


@pytest.mark.parametrize(
    "status,fragment",
    [(401, "rejected the API key"), (429, "rate-limiting"), (500, "returned an error (500)")],
)
def test_provider_errors_are_mapped_without_leaking_the_key(status, fragment):
    def handler(request):
        return httpx.Response(status, text=f"error for key {KEY}")

    with pytest.raises(LLMProviderError) as info:
        run(provider(handler).generate("s", "u", 0.2, 0.9, 10))
    assert fragment in info.value.message
    assert KEY not in info.value.message


def test_network_failure_is_a_provider_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(LLMProviderError):
        run(provider(handler).generate("s", "u", 0.2, 0.9, 10))


def test_vision_uses_image_content_part_when_configured():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "A bar chart."}}]})

    text = run(provider(handler, vision_model="vision-model").describe_image(b"\x89PNG", "Describe", 0.2, 50))
    assert text == "A bar chart."
    part = seen["body"]["messages"][0]["content"][1]
    assert seen["body"]["model"] == "vision-model" and part["image_url"]["url"].startswith("data:image/png;base64,")


def test_without_vision_model_images_are_not_sent():
    def handler(request):
        raise AssertionError("no request expected")

    assert run(provider(handler).describe_image(b"img", "Describe", 0.2, 50)) == ""


def test_missing_configuration_is_rejected():
    with pytest.raises(ConfigurationError):
        OpenAICompatibleProvider(base_url="https://x/v1", api_key="", model="m")
    with pytest.raises(ConfigurationError):
        OpenAICompatibleProvider(base_url="", api_key="k", model="m")


def test_factory_builds_it_from_settings():
    factory.reset_provider_for_testing()
    try:
        settings = Settings(LLM_PROVIDER="openai_compatible", LLM_BASE_URL="https://x/v1", LLM_API_KEY="k", LLM_MODEL="m")
        built = factory.get_llm_provider(settings)
        assert isinstance(built, OpenAICompatibleProvider)
        assert (built.provider_name, built.model_name) == ("openai_compatible", "m")
    finally:
        factory.reset_provider_for_testing()


@pytest.mark.parametrize("effort,expected", [("low", "low"), ("", None), (None, None)])
def test_reasoning_effort_is_sent_only_when_configured(effort, expected):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    run(provider(handler, reasoning_effort=effort).generate("s", "u", temperature=0.2, top_p=0.9, max_output_tokens=50))
    assert seen["body"].get("reasoning_effort") == expected
