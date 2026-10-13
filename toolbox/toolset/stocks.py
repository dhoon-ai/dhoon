"""Market data tools (Yahoo Finance via yfinance)."""

import json

import toolbox_server as tb


# ------------------------------------------------------------------ stocks ---
def _history(ticker: str, period: str):
    import yfinance as yf

    df = yf.Ticker(ticker).history(period=period, auto_adjust=True)
    # Yahoo sometimes adds a row without prices (often today's, for NSE India tickers before or
    # after trading). One NaN there turns every price, return and average into "nan".
    if not df.empty and "Close" in df:
        df = df.dropna(subset=["Close"])
    return df


@tb.mcp.tool()
def stock_price_summary(ticker: str, period: str = "6mo") -> str:
    """Price statistics for a stock: period return, high/low, volatility, max drawdown, volume.
    ticker uses Yahoo symbols: AAPL (US), SHOP.TO (TSX), RELIANCE.NS (NSE India).
    period is one of 1mo, 3mo, 6mo, 1y, 2y, 5y, ytd."""
    df = tb._history(ticker, period)
    if df.empty:
        return f"No data for {ticker}. Check the symbol."
    close = df["Close"]
    ret = close.pct_change().dropna()
    return json.dumps(
        {
            "ticker": ticker,
            "period": period,
            "from": str(close.index[0].date()),
            "to": str(close.index[-1].date()),
            "first_close": round(float(close.iloc[0]), 2),
            "last_close": round(float(close.iloc[-1]), 2),
            "period_return_pct": round(float(close.iloc[-1] / close.iloc[0] - 1) * 100, 2),
            "high": round(float(close.max()), 2),
            "low": round(float(close.min()), 2),
            "annualised_volatility_pct": round(float(ret.std() * 252**0.5 * 100), 2),
            "max_drawdown_pct": round(float((close / close.cummax() - 1).min() * 100), 2),
            "avg_daily_volume": int(df["Volume"].mean()),
        }
    )


@tb.mcp.tool()
def stock_indicators(ticker: str) -> str:
    """Technical indicators for a stock: SMA50, SMA200, RSI14, MACD and the trend regime."""
    df = tb._history(ticker, "1y")
    if len(df) < 200:
        return f"Not enough history for {ticker} (need about 200 trading days)."
    close = df["Close"]
    sma50, sma200 = close.rolling(50).mean().iloc[-1], close.rolling(200).mean().iloc[-1]
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    g, lo = float(gain.iloc[-1]), float(loss.iloc[-1])
    rsi = 100.0 if lo == 0 else 100 - 100 / (1 + g / lo)  # no down days = RSI 100
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal = macd.ewm(span=9, adjust=False).mean()
    last = float(close.iloc[-1])
    return json.dumps(
        {
            "ticker": ticker,
            "last_close": round(last, 2),
            "sma50": round(float(sma50), 2),
            "sma200": round(float(sma200), 2),
            "price_vs_sma200_pct": round((last / float(sma200) - 1) * 100, 2),
            "trend": "SMA50 above SMA200 (uptrend regime)"
            if sma50 > sma200
            else "SMA50 below SMA200 (downtrend regime)",
            "rsi14": round(float(rsi), 1),
            "macd_state": "MACD above signal (bullish momentum)"
            if macd.iloc[-1] > signal.iloc[-1]
            else "MACD below signal (bearish momentum)",
        }
    )


@tb.mcp.tool()
def stock_fundamentals(ticker: str) -> str:
    """Headline fundamentals for a stock: sector, market cap, P/E, P/B, margins, growth, debt, dividend yield."""
    import yfinance as yf

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
    out = {k: info[k] for k in keys if info.get(k) is not None}
    return json.dumps(out) if out else f"No fundamentals available for {ticker}."


@tb.mcp.tool()
def stock_save_history(tickers: str, period: str = "1y") -> str:
    """Save daily closing prices of one or more stocks to a CSV file in the workspace, so
    run_python can chart or analyse them (run_python itself has no internet).
    tickers: comma-separated Yahoo symbols, e.g. "GOOGL,MSFT". period: 1mo, 3mo, 6mo, 1y,
    2y, 5y or ytd. Returns the file name and its columns."""
    import pandas as pd

    names = [t.strip().upper() for t in tickers.split(",") if t.strip()][:5]
    if not names:
        return "Give at least one ticker, e.g. GOOGL,MSFT."
    closes, missing = {}, []
    for t in names:
        df = tb._history(t, period)
        if df.empty:
            missing.append(t)
        else:
            closes[t] = df["Close"].round(2)
    if not closes:
        return f"No data for {', '.join(missing)}. Check the symbols."
    table = pd.DataFrame(closes)
    table.index = pd.to_datetime(table.index).date
    table.index.name = "Date"
    rel = f"data/{'_'.join(closes)}_{period}.csv"
    path = tb._safe_path(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(path)
    note = f" No data for: {', '.join(missing)}." if missing else ""
    return (
        f"Saved {len(table)} days of closing prices to {rel} (columns: Date, {', '.join(closes)}).{note}\n"
        f"In run_python: df = pd.read_csv('{rel}', parse_dates=['Date'], index_col='Date')\n"
        f"Last rows:\n{table.tail(3).to_string()}"
    )
