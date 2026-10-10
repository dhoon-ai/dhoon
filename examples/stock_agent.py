#!/usr/bin/env python3
"""
stock_agent.py - a small, fully local, open-source stock-analysis agent.

The LLM (running in Ollama on this machine) decides WHICH tools to call;
Python does all the maths. Small local models are bad at arithmetic, so
never let the model compute indicators itself.

Setup (inside a venv):
    pip install openai yfinance pandas
Run:
    python stock_agent.py "Analyse AAPL over the last 6 months"
    python stock_agent.py "Compare AAPL and MSFT momentum this year"

Not financial advice. Output is only as good as the data and the model.
"""

import json
import sys

import yfinance as yf
from openai import OpenAI

# Ollama exposes an OpenAI-compatible API locally. No data leaves the box
# except the price download from Yahoo Finance.
MODEL = "qwen3:30b-a3b"  # change to whatever you pulled with `ollama pull`
client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")


# ---------------------------------------------------------------- tools ---
def get_price_summary(ticker: str, period: str = "6mo") -> dict:
    """Download daily prices and return computed statistics (no raw dump)."""
    df = yf.Ticker(ticker).history(period=period, auto_adjust=True)
    if df.empty:
        return {"error": f"No data for {ticker}. Check the symbol (e.g. RY.TO for TSX, RELIANCE.NS for NSE)."}
    close = df["Close"]
    ret = close.pct_change().dropna()
    return {
        "ticker": ticker,
        "period": period,
        "first_date": str(close.index[0].date()),
        "last_date": str(close.index[-1].date()),
        "first_close": round(float(close.iloc[0]), 2),
        "last_close": round(float(close.iloc[-1]), 2),
        "period_return_pct": round(float(close.iloc[-1] / close.iloc[0] - 1) * 100, 2),
        "high": round(float(close.max()), 2),
        "low": round(float(close.min()), 2),
        "annualised_volatility_pct": round(float(ret.std() * (252**0.5) * 100), 2),
        "max_drawdown_pct": round(float((close / close.cummax() - 1).min() * 100), 2),
        "avg_daily_volume": int(df["Volume"].mean()),
    }


def get_indicators(ticker: str) -> dict:
    """Compute common technical indicators on ~1 year of daily data."""
    df = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
    if len(df) < 200:
        return {"error": f"Not enough history for {ticker} (need ~200 trading days)."}
    close = df["Close"]
    sma50 = close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1]
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    g, lo = float(gain.iloc[-1]), float(loss.iloc[-1])
    rsi = 100.0 if lo == 0 else 100 - 100 / (1 + g / lo)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    last = float(close.iloc[-1])
    return {
        "ticker": ticker,
        "last_close": round(last, 2),
        "sma50": round(float(sma50), 2),
        "sma200": round(float(sma200), 2),
        "price_vs_sma200_pct": round((last / float(sma200) - 1) * 100, 2),
        "trend": "golden-cross regime (SMA50 > SMA200)" if sma50 > sma200 else "death-cross regime (SMA50 < SMA200)",
        "rsi14": round(float(rsi), 1),
        "macd": round(float(macd.iloc[-1]), 3),
        "macd_signal": round(float(signal.iloc[-1]), 3),
        "macd_state": "bullish (MACD above signal)"
        if macd.iloc[-1] > signal.iloc[-1]
        else "bearish (MACD below signal)",
    }


def get_fundamentals(ticker: str) -> dict:
    """Return a few headline fundamentals, where Yahoo has them."""
    info = yf.Ticker(ticker).info or {}
    keys = [
        "longName",
        "sector",
        "industry",
        "currency",
        "marketCap",
        "trailingPE",
        "forwardPE",
        "priceToBook",
        "dividendYield",
        "profitMargins",
        "revenueGrowth",
        "debtToEquity",
        "fiftyTwoWeekHigh",
        "fiftyTwoWeekLow",
    ]
    out = {k: info.get(k) for k in keys if info.get(k) is not None}
    return out or {"error": f"No fundamentals available for {ticker}."}


TOOLS_IMPL = {
    "get_price_summary": get_price_summary,
    "get_indicators": get_indicators,
    "get_fundamentals": get_fundamentals,
}

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_price_summary",
            "description": "Price statistics for a ticker over a period: return, high/low, volatility, drawdown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Yahoo symbol, e.g. AAPL, SHOP.TO, RELIANCE.NS"},
                    "period": {"type": "string", "enum": ["1mo", "3mo", "6mo", "1y", "2y", "5y", "ytd"]},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_indicators",
            "description": "Technical indicators: SMA50, SMA200, RSI14, MACD, trend regime.",
            "parameters": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fundamentals",
            "description": "Headline fundamentals: P/E, P/B, margins, growth, debt, dividend yield.",
            "parameters": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
        },
    },
]

SYSTEM = (
    "You are a careful equity analyst. Always call tools to get numbers; never invent "
    "or compute figures yourself. Call every tool that is relevant, then write a concise "
    "report: 1) snapshot, 2) trend & momentum, 3) risk, 4) valuation (if available), "
    "5) what would change the picture. Quote the numbers the tools returned. "
    "Do not give buy/sell instructions; end with a one-line 'not financial advice' note."
)


# ---------------------------------------------------------------- agent loop ---
def run(question: str, max_steps: int = 8) -> str:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    for _ in range(max_steps):
        resp = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS_SCHEMA, temperature=0.2)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return msg.content or ""
        messages.append(msg.model_dump(exclude_none=True))
        for call in msg.tool_calls:
            name, args = call.function.name, {}
            try:
                args = json.loads(call.function.arguments or "{}")
                result = TOOLS_IMPL[name](**args)
            except Exception as e:  # feed errors back so the model can recover
                result = {"error": f"{type(e).__name__}: {e}"}
            print(f"  [tool] {name}({args})", file=sys.stderr)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, default=str)})
    return "Stopped: too many steps without a final answer."


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Analyse AAPL over the last 6 months."
    print(run(q))
