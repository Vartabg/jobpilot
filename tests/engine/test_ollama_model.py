import pytest
import requests

from jobpilot.engine.adapters.ollama_model import OllamaModel
from jobpilot.engine.domain.fit import ModelError

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


class FakeResponse:
    def __init__(self, status=200, payload=None, raises=False):
        self.status_code, self.payload, self.raises = status, payload, raises

    def json(self):
        if self.raises:
            raise ValueError("not json")
        return self.payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def post(self, url, json, timeout):
        self.calls.append((url, json, timeout))
        if self.error:
            raise self.error
        return self.response


def test_request_shape_and_answer():
    session = FakeSession(
        FakeResponse(payload={"message": {"content": '{"ok": true}'}})
    )
    model = OllamaModel("qwen3:14b", session=session)
    assert model.generate_json("hello", schema=SCHEMA) == {"ok": True}
    ((url, body, _),) = session.calls
    assert url == "http://127.0.0.1:11434/api/chat"
    assert body["model"] == "qwen3:14b" and body["format"] == SCHEMA
    assert body["think"] is False and body["stream"] is False
    assert body["options"]["temperature"] == 0
    assert model.name == "ollama:qwen3:14b" and model.is_local


def test_thinking_blocks_are_stripped():
    content = '<think>let me see</think>\n{"ok": false}'
    session = FakeSession(FakeResponse(payload={"message": {"content": content}}))
    assert OllamaModel("qwen3:14b", session=session).generate_json(
        "x", schema=SCHEMA
    ) == {"ok": False}


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse(status=500), None),
        (FakeResponse(raises=True), None),
        (FakeResponse(payload={"message": {"content": "not json"}}), None),
        (FakeResponse(payload={"unexpected": 1}), None),
        (None, requests.ConnectionError()),
    ],
)
def test_failures_are_model_errors(response, error):
    with pytest.raises(ModelError):
        OllamaModel("qwen3:14b", session=FakeSession(response, error)).generate_json(
            "x", schema=SCHEMA
        )


def test_cloud_models_are_flagged_and_names_are_required():
    assert not OllamaModel("kimi-k3:cloud", session=FakeSession()).is_local
    with pytest.raises(ValueError):
        OllamaModel("")
