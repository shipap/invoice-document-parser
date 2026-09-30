import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.pdf import DocumentError
from app.providers import OpenAIProvider, make_provider


def test_real_provider_request_and_structured_response(monkeypatch):
    original_client = httpx.AsyncClient

    def handle(request):
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]["schema"]
        assert set(schema["required"]) == set(schema["properties"])
        assert "default" not in json.dumps(schema)
        assert request.url.host == "api.openai.com"
        assert request.headers["authorization"] == "Bearer fictional-test-key"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"total":"123.45"}'}}]}
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    invoice = asyncio.run(
        OpenAIProvider(Settings(openai_api_key="fictional-test-key", _env_file=None)).extract(
            "test invoice"
        )
    )
    assert str(invoice.total) == "123.45"


def test_provider_failure_does_not_expose_response(monkeypatch):
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(401, text="sensitive provider error")
            ),
            **kwargs,
        ),
    )
    with pytest.raises(DocumentError) as caught:
        asyncio.run(
            OpenAIProvider(Settings(openai_api_key="fictional-test-key", _env_file=None)).extract(
                "test"
            )
        )
    assert caught.value.code == "provider_error"
    assert "sensitive" not in str(caught.value)


def test_provider_configuration_requires_credentials():
    with pytest.raises(ValueError):
        make_provider(Settings(provider="openai", openai_api_key=None, _env_file=None))
