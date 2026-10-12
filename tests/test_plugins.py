"""Private plugins in plugins/ are loaded by the toolbox and given to the agent."""

import asyncio
import importlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "toolbox"))
sys.path.insert(0, str(REPO / "scripts"))

TOOLS = '''import toolbox_server as tb
from helper import shout


@tb.mcp.tool()
def plugin_echo(text: str) -> str:
    """Echo text back, loudly."""
    return shout(text)
'''


def make_plugins(tmp_path):
    good = tmp_path / "plugins" / "demo"
    good.mkdir(parents=True)
    (good / "tools.py").write_text(TOOLS)
    (good / "helper.py").write_text("def shout(t):\n    return t.upper() + '!'\n")
    (good / "instructions.md").write_text("- Use plugin_echo when asked to shout.\n")
    (good / "starters.txt").write_text("Shout hello with the demo plugin\n")
    broken = tmp_path / "plugins" / "broken"
    broken.mkdir()
    (broken / "tools.py").write_text("raise RuntimeError('oops')\n")
    return tmp_path / "plugins"


def test_toolbox_loads_plugins_and_skips_broken_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("PLUGINS_DIR", str(make_plugins(tmp_path)))
    import toolbox_server

    tb = importlib.reload(toolbox_server)
    assert tb.LOADED_PLUGINS == ["demo"]  # "broken" was skipped, the toolbox still started
    names = {t.name for t in asyncio.run(tb.mcp.list_tools())}
    assert "plugin_echo" in names and "web_search" in names
    assert "HELLO!" in str(asyncio.run(tb.mcp.call_tool("plugin_echo", {"text": "hello"})))
    monkeypatch.delenv("PLUGINS_DIR")
    importlib.reload(toolbox_server)


def test_agent_gets_plugin_tools_instructions_and_starters(tmp_path, monkeypatch):
    monkeypatch.setenv("PLUGINS_DIR", str(make_plugins(tmp_path)))
    import create_agent

    ca = importlib.reload(create_agent)
    payload = ca.agent_payload("m:instruct")
    assert "plugin_echo_mcp_toolbox" in payload["tools"] and "web_search_mcp_toolbox" in payload["tools"]
    assert payload["instructions"].endswith("- Use plugin_echo when asked to shout.")
    assert "Shout hello with the demo plugin" in payload["conversation_starters"]
    monkeypatch.delenv("PLUGINS_DIR")
    importlib.reload(create_agent)


def test_plugins_folder_is_git_ignored_except_readme():
    rules = (REPO / ".gitignore").read_text()
    assert "plugins/*" in rules and "!plugins/README.md" in rules
