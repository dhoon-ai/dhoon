"""Fast first answer: the toolbox passes chats to Ollama, keeps the Assistant's instructions
and tool list, and warmup.py has Ollama read them in advance. No real Ollama needed."""

import datetime as dt
import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolbox"))

SYSTEM = "You are a helpful assistant.\n\nToday is Monday, October 12, 2026. For the exact time, call current_datetime."
TOOLS = [
    {"type": "function", "function": {"name": "web_search_mcp_toolbox", "description": "Search", "parameters": {}}}
]


def chat_request(user="Send me a test e-mail", tools=TOOLS, model="qwen3:30b"):
    return {"model": model, "stream": True, "tools": tools,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}  # fmt: skip


@pytest.fixture()
def warm(tmp_path, monkeypatch):
    monkeypatch.setenv("WARM_DIR", str(tmp_path / "warm"))
    monkeypatch.setenv("WORKSPACE", str(tmp_path / "ws"))
    import warmup

    return importlib.reload(warmup)


def test_only_the_instructions_and_tools_are_kept_never_the_chat(warm):
    assert warm.remember(json.dumps(chat_request()).encode())
    saved = json.loads(next((warm.WARM_DIR).glob("*.json")).read_text())
    assert saved["system"][0]["content"] == SYSTEM and saved["tools"] == TOOLS
    assert "test e-mail" not in json.dumps(saved)
    assert not warm.remember(json.dumps(chat_request(tools=[])).encode())  # e.g. title generation
    assert not warm.remember(b"not json")


def test_warm_up_sends_the_same_words_with_todays_date(warm):
    warm.remember(json.dumps(chat_request()).encode())
    f = next(warm.WARM_DIR.glob("*.json"))
    f.write_text(json.dumps({**json.loads(f.read_text()), "date": "2026-10-12"}))
    sent = []

    def ollama(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "H"}}]})

    lines = warm.warm_up(httpx.MockTransport(ollama), today=dt.date(2026, 10, 13))
    assert "instructions and 1 tools read in advance" in lines[0]
    body = sent[0]
    assert body["tools"] == TOOLS and body["max_tokens"] == 1 and body["stream"] is False
    assert "Today is Tuesday, October 13, 2026." in body["messages"][0]["content"]
    assert body["messages"][-1]["role"] == "user"


def test_nothing_saved_yet_is_fine(warm):
    assert warm.warm_up(httpx.MockTransport(lambda r: httpx.Response(500))) == []


def test_chats_pass_through_the_toolbox_unchanged(warm, monkeypatch):
    import toolbox_server

    tb = importlib.reload(toolbox_server)
    seen = []

    async def ollama(request):
        seen.append((request.method, str(request.url), request.content))
        if request.url.path.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": "qwen3:30b"}]})
        return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\ndata: [DONE]\n\n',
                              headers={"content-type": "text/event-stream"})  # fmt: skip

    tb._OLLAMA_TRANSPORT = httpx.MockTransport(ollama)
    from starlette.testclient import TestClient

    with TestClient(tb.mcp.streamable_http_app(), base_url="http://host.docker.internal:8001") as client:
        r = client.post("/ollama/v1/chat/completions", json=chat_request())
        assert r.status_code == 200 and "data: [DONE]" in r.text
        assert r.headers["content-type"].startswith("text/event-stream")
        tags = client.get("/ollama/api/tags")
        assert tags.json()["models"][0]["name"] == "qwen3:30b"
    assert seen[0][1] == "http://127.0.0.1:11434/v1/chat/completions"
    assert json.loads(seen[0][2])["messages"][1]["content"] == "Send me a test e-mail"  # unchanged
    assert list(warm.WARM_DIR.glob("*.json"))  # instructions kept for the warm-up


def test_tool_list_has_no_redundant_titles():
    import asyncio

    import toolbox_server

    tb = importlib.reload(toolbox_server)
    schemas = json.dumps([t.input_schema for t in asyncio.run(tb.mcp.list_tools())])
    assert '"title": "' not in schemas  # a parameter called title stays


def test_only_the_agent_used_last_is_read_ahead(warm):
    import os
    import time

    quick_tools = [{"type": "function", "function": {"name": "weather_mcp_toolbox"}}]
    warm.remember(json.dumps(chat_request()).encode())  # the Assistant
    time.sleep(0.01)
    warm.remember(json.dumps(chat_request(tools=quick_tools)).encode())  # then the Quick Assistant
    assert len(list(warm.WARM_DIR.glob("*.json"))) == 2
    latest = warm.latest_per_model()
    assert len(latest) == 1 and json.loads(latest[0].read_text())["tools"] == quick_tools
    past = time.time() - 60
    for f in warm.WARM_DIR.glob("*.json"):
        os.utime(f, (past, past))
    warm.remember(json.dumps(chat_request()).encode())  # back to the Assistant (same text)
    assert json.loads(warm.latest_per_model()[0].read_text())["tools"] == TOOLS


def test_only_the_models_in_use_are_warmed_and_the_main_one_last(warm, monkeypatch):
    import json as _json
    import os as _os

    for model, age in (
        ("gemma4:26b", 3),
        ("qwen3:30b-a3b-instruct-2507-q4_K_M", 1),
        ("qwen3:4b-instruct-2507-q4_K_M", 2),
    ):
        f = warm.WARM_DIR / (model.replace(":", "_") + ".json")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_json.dumps({"model": model, "system": [], "tools": [], "date": "2026-10-18"}))
        _os.utime(f, (1000 - age, 1000 - age))
    monkeypatch.setenv("WARM_MODELS", "gemma4:26b qwen3:4b-instruct-2507-q4_K_M")
    order = [_json.loads(f.read_text())["model"] for f in warm.latest_per_model()]
    assert order == ["qwen3:4b-instruct-2507-q4_K_M", "gemma4:26b"]  # the old 30b model is left alone
    monkeypatch.delenv("WARM_MODELS")
    assert len(warm.latest_per_model()) == 3  # run by hand without the list: everything, as before


def test_every_request_slot_gets_the_instructions(warm, monkeypatch):
    import threading

    warm.remember(json.dumps(chat_request()).encode())
    monkeypatch.setenv("WARM_PARALLEL", "2")
    seen, both_in = [], threading.Barrier(2, timeout=5)

    def ollama(request):
        seen.append(json.loads(request.content)["messages"][0]["content"])
        both_in.wait()  # only returns when the two requests are in Ollama at the same time
        return httpx.Response(200, json={"choices": [{"message": {"content": "H"}}]})

    lines = warm.warm_up(httpx.MockTransport(ollama))
    assert len(seen) == 2 and seen[0] == seen[1]
    assert "(in all 2 slots," in lines[0]
    monkeypatch.setenv("WARM_PARALLEL", "1")
    seen.clear()
    assert (
        "slots" not in warm.warm_up(httpx.MockTransport(lambda r: (seen.append(1), httpx.Response(200, json={}))[1]))[0]
    )
    assert len(seen) == 1


def test_a_cloud_chat_falls_back_to_the_model_on_this_computer(warm, monkeypatch):
    import toolbox_server

    tb = importlib.reload(toolbox_server)
    monkeypatch.setattr(tb, "MODEL_FALLBACK", "gemma4:26b")
    tb._cloud["rest_until"] = 0.0
    seen, cloud_ok = [], {"ok": False}

    async def ollama(request):
        model = json.loads(request.content).get("model")
        seen.append(model)
        if model.endswith("cloud") and not cloud_ok["ok"]:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, content=f'data: {{"model":"{model}"}}\n\ndata: [DONE]\n\n'.encode(),
                              headers={"content-type": "text/event-stream"})  # fmt: skip

    tb._OLLAMA_TRANSPORT = httpx.MockTransport(ollama)
    from starlette.testclient import TestClient

    with TestClient(tb.mcp.streamable_http_app(), base_url="http://host.docker.internal:8001") as client:
        r = client.post("/ollama/v1/chat/completions", json=chat_request(model="beam:cloud"))
        assert r.status_code == 200 and "gemma4:26b" in r.text  # answered on this computer
        assert seen == ["beam:cloud", "gemma4:26b"]
        cloud_ok["ok"] = True
        client.post("/ollama/v1/chat/completions", json=chat_request(model="beam:cloud"))
        assert seen[-1] == "gemma4:26b"  # the cloud rests after a failure: straight to this computer
        tb._cloud["rest_until"] = 0.0
        r = client.post("/ollama/v1/chat/completions", json=chat_request(model="beam:cloud"))
        assert seen[-1] == "beam:cloud" and "beam:cloud" in r.text  # rested: the cloud again
        client.post("/ollama/v1/chat/completions", json=chat_request(model="qwen3:30b"))
        assert seen[-1] == "qwen3:30b"  # a model on this computer: passed on as it is
    assert not [f for f in warm.WARM_DIR.glob("*.json") if "cloud" in f.name]  # nothing to read ahead in the cloud
