# Examples

## `stock_agent.py`: a minimal agent with no UI

A single-file agent loop that talks straight to Ollama's OpenAI-compatible API.
It shows how tool calling works under the hood: the model asks for a tool,
Python runs it, the result goes back to the model, and repeat until it answers.

```bash
cd ~/dhoon
runtime/venv/bin/pip install openai
runtime/venv/bin/python examples/stock_agent.py "Analyse AAPL over the last 6 months"
```

The main system (LibreChat + toolbox) does the same thing with a web UI and more tools.

## Prompts to try with the Assistant agent

**Easy**
- What is today's date and day of the week?
- Calculate the monthly payment on a 400,000 loan at 4.5% interest for 25 years.

**Research**
- Find three open-source tools for backing up a Linux PC and compare them in a table. Cite sources.

**Files and data**
- Create shopping/list.md with a weekly grocery list for two people.
- Read transactions.csv, total spending by category, and save a bar chart as spending.png.

**Multi-step**
- Analyse AAPL: 6-month price summary, indicators and fundamentals. Search for recent news. Write a report to reports/AAPL.md.
- Compare MSFT and GOOGL on trend, risk and valuation in a table.
