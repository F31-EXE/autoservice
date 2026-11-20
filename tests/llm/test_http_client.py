"""The real client against a fake HTTP transport: checks the wire format, no network."""

import json

import httpx
import pytest

from apps.llm.client import LLMError, OpenAICompatibleClient


@pytest.fixture
def configured(settings):
    settings.LLM_API_KEY = "test-key"
    settings.LLM_FOLDER_ID = "b1gfolder"
    settings.LLM_BASE_URL = "https://llm.api.cloud.yandex.net/v1"


def test_request_format_and_usage(configured):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": '{"recommendations": []}'}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 30, "total_tokens": 1230},
        })  # fmt: skip

    client = OpenAICompatibleClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    response = client.complete([{"role": "user", "content": "привет"}], "yandexgpt", 100)

    assert seen["url"] == "https://llm.api.cloud.yandex.net/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer test-key"
    assert seen["headers"]["openai-project"] == "b1gfolder"
    assert seen["body"]["model"] == "gpt://b1gfolder/yandexgpt/latest"
    assert seen["body"]["temperature"] == 0 and seen["body"]["max_tokens"] == 100
    assert response.text == '{"recommendations": []}'
    assert (response.input_tokens, response.output_tokens) == (1200, 30)


def test_http_error_is_normalized_without_payload(configured):
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "secret details +79000000001"}})

    client = OpenAICompatibleClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(LLMError) as exc:
        client.complete([{"role": "user", "content": "x"}], "m", 10)
    assert str(exc.value) == "AuthenticationError 401"
