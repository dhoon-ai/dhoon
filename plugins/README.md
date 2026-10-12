# Private plugins

Put your own tools here when you don't want them in this public repo. Everything in this
folder except this README is ignored by git, so it stays on your computer.

Each plugin is a folder:

```
plugins/
  my-plugin/
    tools.py          extra agent tools (required)
    instructions.md   extra lines for the agent's instructions (optional)
    starters.txt      extra conversation starters, one per line (optional)
```

`tools.py`:

```python
import toolbox_server as tb


@tb.mcp.tool()
def my_tool(text: str) -> str:
    """What the tool does, in plain words: the AI reads this to decide when to use it."""
    return text.upper()
```

The toolbox loads every `plugins/*/tools.py` when it starts (a broken plugin is skipped and
logged, it never stops the toolbox). After adding or changing a plugin:

```bash
sudo systemctl restart ai-toolbox       # load the new tools
bash scripts/06_create_agent.sh         # give them (and instructions.md) to the Assistant
```

Check it loaded: `journalctl -u ai-toolbox -n 30 | grep PLUGIN`. A plugin can keep its own
git history: run `git init` inside its folder (this repo ignores it).
