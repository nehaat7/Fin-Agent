"""Financial data tools backed by yfinance. Every tool returns JSON-serialisable data."""
import math
from datetime import datetime, date

import pandas as pd
import yfinance as yf


def _clean(value):
    """Recursively convert pandas/numpy values into plain JSON-safe Python."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()[:10]
    if hasattr(value, "item"):  # numpy scalar
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _ticker(symbol: str) -> yf.Ticker:
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("ticker is required")
    return yf.Ticker(symbol)


def get_quote(ticker: str) -> dict:
    info = _ticker(ticker).info
    if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
        raise ValueError(f"No quote data found for '{ticker}'. Check the symbol (try search_ticker).")
    keys = [
        "symbol", "shortName", "currency", "exchange", "currentPrice", "regularMarketPrice",
        "previousClose", "regularMarketChangePercent", "dayLow", "dayHigh",
        "fiftyTwoWeekLow", "fiftyTwoWeekHigh", "marketCap", "trailingPE", "forwardPE",
        "priceToBook", "dividendYield", "beta", "volume", "averageVolume",
        "targetMeanPrice", "recommendationKey",
    ]
    return _clean({k: info.get(k) for k in keys})


def get_price_history(ticker: str, period: str = "6mo", interval: str = "1d") -> dict:
    hist = _ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    if hist.empty:
        raise ValueError(f"No price history for '{ticker}' (period={period}, interval={interval}).")
    close = hist["Close"]
    returns = close.pct_change().dropna()
    periods_per_year = {"1d": 252, "5d": 52, "1wk": 52, "1mo": 12, "3mo": 4}.get(interval, 252)
    drawdown = close / close.cummax() - 1
    stats = {
        "start_date": hist.index[0], "end_date": hist.index[-1],
        "start_price": close.iloc[0], "end_price": close.iloc[-1],
        "total_return_pct": (close.iloc[-1] / close.iloc[0] - 1) * 100,
        "annualized_volatility_pct": returns.std() * math.sqrt(periods_per_year) * 100 if len(returns) > 1 else None,
        "max_drawdown_pct": drawdown.min() * 100,
        "period_high": hist["High"].max(), "period_low": hist["Low"].min(),
        "observations": len(hist),
    }
    # Keep the payload small: at most ~30 evenly spaced closing prices.
    step = max(1, len(hist) // 30)
    sampled = hist.iloc[::step]
    series = [{"date": idx, "close": round(float(c), 4)} for idx, c in sampled["Close"].items()]
    return _clean({"ticker": ticker.upper(), "period": period, "interval": interval,
                   "stats": stats, "closes_sampled": series})


def get_company_profile(ticker: str) -> dict:
    info = _ticker(ticker).info
    keys = ["symbol", "longName", "sector", "industry", "country", "website",
            "fullTimeEmployees", "longBusinessSummary"]
    data = {k: info.get(k) for k in keys}
    if data.get("longBusinessSummary"):
        data["longBusinessSummary"] = data["longBusinessSummary"][:1500]
    return _clean(data)


_STATEMENT_ROWS = {
    "income": ["Total Revenue", "Gross Profit", "Operating Income", "Net Income", "Diluted EPS", "EBITDA"],
    "balance": ["Total Assets", "Total Liabilities Net Minority Interest", "Stockholders Equity",
                "Cash And Cash Equivalents", "Total Debt", "Net Debt"],
    "cashflow": ["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow",
                 "Repurchase Of Capital Stock", "Cash Dividends Paid"],
}


def get_financial_statements(ticker: str, statement: str = "income", quarterly: bool = False) -> dict:
    statement = statement.lower()
    if statement not in _STATEMENT_ROWS:
        raise ValueError("statement must be one of: income, balance, cashflow")
    t = _ticker(ticker)
    df = {
        ("income", False): lambda: t.income_stmt, ("income", True): lambda: t.quarterly_income_stmt,
        ("balance", False): lambda: t.balance_sheet, ("balance", True): lambda: t.quarterly_balance_sheet,
        ("cashflow", False): lambda: t.cashflow, ("cashflow", True): lambda: t.quarterly_cashflow,
    }[(statement, bool(quarterly))]()
    if df is None or df.empty:
        raise ValueError(f"No {statement} statement available for '{ticker}'.")
    df = df.iloc[:, :4]  # latest 4 periods
    rows = [r for r in _STATEMENT_ROWS[statement] if r in df.index]
    out = {str(col)[:10]: {r: df.at[r, col] for r in rows} for col in df.columns}
    return _clean({"ticker": ticker.upper(), "statement": statement,
                   "frequency": "quarterly" if quarterly else "annual", "periods": out})


def get_news(ticker: str, limit: int = 5) -> dict:
    limit = max(1, min(int(limit), 10))
    items = _ticker(ticker).news or []
    if not items:  # Ticker.news is sometimes empty; the search endpoint is more reliable
        items = yf.Search(ticker.strip().upper(), max_results=1, news_count=limit).news or []
    out = []
    for item in items[:limit]:
        c = item.get("content", item)
        url = (c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else c.get("link")
        out.append({
            "title": c.get("title"),
            "publisher": (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else c.get("publisher"),
            "published": c.get("pubDate") or (datetime.utcfromtimestamp(c["providerPublishTime"]).isoformat()
                                               if c.get("providerPublishTime") else None),
            "summary": (c.get("summary") or "")[:300],
            "url": url,
        })
    return _clean({"ticker": ticker.upper(), "articles": out})


def search_ticker(query: str, limit: int = 5) -> dict:
    res = yf.Search(query, max_results=max(1, min(int(limit), 10)), news_count=0)
    quotes = [{"symbol": q.get("symbol"), "name": q.get("shortname") or q.get("longname"),
               "exchange": q.get("exchange"), "type": q.get("quoteType")} for q in res.quotes]
    return _clean({"query": query, "results": quotes})


TICKER = {"type": "string", "description": "Ticker symbol, e.g. AAPL, MSFT, ^GSPC, BTC-USD"}

TOOLS = {
    "get_quote": {
        "fn": get_quote,
        "description": "Latest quote and key valuation metrics (price, change, market cap, P/E, 52-week range, analyst target) for one ticker.",
        "inputSchema": {"type": "object", "properties": {"ticker": TICKER}, "required": ["ticker"]},
    },
    "get_price_history": {
        "fn": get_price_history,
        "description": "Historical prices plus computed stats (total return, annualized volatility, max drawdown, high/low) over a period.",
        "inputSchema": {"type": "object", "properties": {
            "ticker": TICKER,
            "period": {"type": "string", "enum": ["5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "ytd", "max"], "default": "6mo"},
            "interval": {"type": "string", "enum": ["1d", "1wk", "1mo"], "default": "1d"},
        }, "required": ["ticker"]},
    },
    "get_company_profile": {
        "fn": get_company_profile,
        "description": "Company profile: name, sector, industry, country, employees and business summary.",
        "inputSchema": {"type": "object", "properties": {"ticker": TICKER}, "required": ["ticker"]},
    },
    "get_financial_statements": {
        "fn": get_financial_statements,
        "description": "Key lines from the income statement, balance sheet or cash-flow statement for the latest 4 annual or quarterly periods.",
        "inputSchema": {"type": "object", "properties": {
            "ticker": TICKER,
            "statement": {"type": "string", "enum": ["income", "balance", "cashflow"], "default": "income"},
            "quarterly": {"type": "boolean", "default": False},
        }, "required": ["ticker"]},
    },
    "get_news": {
        "fn": get_news,
        "description": "Recent news headlines for a ticker.",
        "inputSchema": {"type": "object", "properties": {
            "ticker": TICKER, "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        }, "required": ["ticker"]},
    },
    "search_ticker": {
        "fn": search_ticker,
        "description": "Find ticker symbols by company name or keyword (use when the user gives a company name, not a symbol).",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        }, "required": ["query"]},
    },
}
