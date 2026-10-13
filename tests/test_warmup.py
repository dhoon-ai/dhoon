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


def chat_request(user="Send me a test e-mail", tools=TOOLS):
    return {"model": "qwen3:30b", "stream": True, "tools": tools,
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
    assert '"title"' not in schemas
