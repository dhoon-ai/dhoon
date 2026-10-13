# Adding new skills (tools) to your agent

Every skill the agent has is a Python function in one of the files in `toolbox/toolset/`
(one file per job: `everyday.py`, `web.py`, `files.py`, `documents.py`, `planner.py`,
`stocks.py`, `mail.py`). The AI reads each function's **name**, **parameters** and
**docstring** to decide when and how to call it. Write those for the AI, not for a programmer.

## The pattern

Add to the fitting file in `toolbox/toolset/` (or a new file there, added to `TOOLSETS` in
`toolbox_server.py`):

```python
@tb.mcp.tool()
def sunrise(city: str) -> str:
    """Sunrise and sunset today for a city. Use when the user asks about daylight."""
    ...do the work in Python...
    return "Sunrise 07:24, sunset 18:35"
```

`tb` is the toolbox core (`import toolbox_server as tb`): shared settings and helpers such as
`tb.WORKSPACE`, `tb.MAX_TEXT` and `tb._safe_path`. Then:

```bash
sudo systemctl restart ai-toolbox
bash scripts/06_create_agent.sh      # gives the new tool to the Assistant
```

## Rules that make tools work well with small local models

1. **Return short summaries, not raw data.** Your CPU has to read every character.
   Return computed numbers (like `stock_price_summary` does), not 200 rows.
   Keep outputs under a few thousand characters.
2. **Do the maths in Python.** Never return raw numbers and hope the model calculates
   correctly.
3. **Use simple parameter types** (`str`, `int`, `float`, `bool`) with sensible defaults.
4. **Say when to use the tool** in the first line of the docstring.
5. **Return errors as text** ("No data for XYZ. Check the symbol.") so the model can
   recover. Unhandled exceptions also reach the model, but less clearly.
6. **Fewer tools = faster, smarter agent.** Every tool's description is sent with every
   request. If you add many, make separate agents with only the tools each one needs.

## Safety

- Anything that **sends, deletes, buys or changes** something outside the workspace
  should not be a tool unless you add your own confirmation step.
- Text from the internet is untrusted. Tools that fetch web content should label it
  (see `read_webpage`), and an agent that browses the web should not also have
  powerful tools.
- Keep file access inside `WORKSPACE` with `_safe_path()`.

## Testing a tool without the AI

```bash
cd ~/inglenook
runtime/venv/bin/python -c "import sys; sys.path.insert(0,'toolbox'); import toolbox_server as t; print(t.sunrise('London'))"
```

Add a test in `tests/test_toolbox.py` too, then run `pytest`.

## Ideas

| Tool | What it does |
|---|---|
| `summarise_folder(path)` | List new files in a folder and summarise text ones |
| `watch_page(url)` | Save a page's text and report what changed since last time |
| `expenses_by_month(csv)` | Total a bank export by category and month |
| `rss_digest(feeds)` | Fetch RSS feeds and return today's headlines |
| `ping_hosts()` | Check which devices on your home network are up |
| `watchlist_report()` | Run the stock tools over a list of tickers and write a report |

## Private tools (plugins)

To keep a tool out of the public repo (personal, experimental or sensitive), make it a
plugin instead of editing the toolbox: a folder in `plugins/` with a `tools.py`.
Everything in `plugins/` except its README is git-ignored. See
[plugins/README.md](../plugins/README.md) for the layout and an example.

