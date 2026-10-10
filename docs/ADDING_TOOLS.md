# Adding new skills (tools) to your agent

Every skill the agent has is a Python function in `toolbox/toolbox_server.py`.
The AI reads each function's **name**, **parameters** and **docstring** to decide
when and how to call it. Write those for the AI, not for a programmer.

## The pattern

```python
@mcp.tool()
def weather_now(city: str) -> str:
    """Current weather for a city. Use when the user asks about weather today."""
    r = httpx.get("https://wttr.in/" + city, params={"format": "3"}, timeout=20)
    r.raise_for_status()
    return r.text.strip()
```

Then:

```bash
sudo systemctl restart ai-toolbox
```

In LibreChat, open your agent in the **Agent Builder**, tick the new tool, and **Save**.

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
cd ~/ai-kit
runtime/venv/bin/python -c "import sys; sys.path.insert(0,'toolbox'); import toolbox_server as t; print(t.weather_now('London'))"
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
