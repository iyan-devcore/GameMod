"""How the Gemini client moves between models. No network calls."""
import json

import pytest

from gamemod.core.translate.base import ContentBlocked, Overloaded, RateLimited, RequestLine, TranslatorError
from gamemod.core.translate.gemini import GeminiTranslator


class Reply:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def ok(text='[{"id": 1, "hi": "ठीक"}]'):
    return Reply(
        200,
        {
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4},
        },
    )


def daily_limit():
    return Reply(429, {"error": {"message": "requests per day limit exceeded", "details": []}})


def minute_limit():
    return Reply(
        429,
        {
            "error": {
                "message": "Too many requests",
                "details": [
                    {"violations": [{"quotaId": "GenerateRequestsPerMinute"}]},
                    {"retryDelay": "7s"},
                ],
            }
        },
    )


def busy():
    return Reply(503, {"error": {"message": "high demand"}})


LINE = [RequestLine(1, "Okay.")]


def client(monkeypatch, replies):
    translator = GeminiTranslator(api_key="SECRETKEY", models=("a", "b"))
    calls = []

    def fake_post(model, *_args):
        calls.append(model)
        return replies[len(calls) - 1]

    monkeypatch.setattr(translator, "_post", fake_post)
    return translator, calls


def test_uses_first_model_when_it_works(monkeypatch):
    translator, calls = client(monkeypatch, [ok()])
    replies, usage = translator.translate("s", LINE, [])
    assert replies == {1: "ठीक"} and calls == ["a"] and usage.output_tokens == 4
    assert translator.model == "a"


def test_busy_reply_makes_one_call_then_next_attempt_uses_the_other_model(monkeypatch):
    translator, calls = client(monkeypatch, [busy(), ok(), ok()])
    with pytest.raises(Overloaded):
        translator.translate("s", LINE, [])
    assert calls == ["a"], "a busy reply uses up daily quota, so it must not retry straight away"

    translator.translate("s", LINE, [])
    translator.translate("s", LINE, [])
    assert calls == ["a", "b", "b"], "stay on the model that is working"
    assert translator.model == "b"


def test_busy_model_is_still_available_later(monkeypatch):
    translator, calls = client(monkeypatch, [busy(), busy(), ok()])
    for _ in range(2):
        with pytest.raises(Overloaded):
            translator.translate("s", LINE, [])
    translator.translate("s", LINE, [])
    assert calls == ["a", "b", "a"]


def test_model_out_of_quota_is_not_tried_again(monkeypatch):
    translator, calls = client(monkeypatch, [daily_limit(), ok(), ok()])
    translator.translate("s", LINE, [])
    translator.translate("s", LINE, [])
    assert calls == ["a", "b", "b"]


def test_all_models_out_of_quota_reports_the_daily_limit(monkeypatch):
    translator, _ = client(monkeypatch, [daily_limit(), daily_limit()])
    with pytest.raises(RateLimited) as error:
        translator.translate("s", LINE, [])
    assert error.value.daily


def test_minute_limit_is_passed_up_with_its_wait(monkeypatch):
    translator, calls = client(monkeypatch, [minute_limit()])
    with pytest.raises(RateLimited) as error:
        translator.translate("s", LINE, [])
    assert not error.value.daily and error.value.retry_after == 7
    assert calls == ["a"]


def test_refused_reply_raises_blocked(monkeypatch):
    refused = Reply(200, {"promptFeedback": {"blockReason": "OTHER"}})
    translator, _ = client(monkeypatch, [refused])
    with pytest.raises(ContentBlocked):
        translator.translate("s", LINE, [])


def test_bad_json_counts_as_no_translations(monkeypatch):
    translator, _ = client(monkeypatch, [ok("not json")])
    assert translator.translate("s", LINE, [])[0] == {}


def test_api_key_never_appears_in_errors(monkeypatch):
    leak = Reply(400, {"error": {"message": "bad key SECRETKEY"}})
    translator, _ = client(monkeypatch, [leak])
    with pytest.raises(TranslatorError) as error:
        translator.translate("s", LINE, [])
    assert "SECRETKEY" not in str(error.value)
    assert json.dumps(str(error.value)).count("***") == 1
