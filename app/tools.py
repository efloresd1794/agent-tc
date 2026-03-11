import logging
import time
from datetime import datetime, timedelta
from typing import Annotated

import requests
import yfinance as yf
from langchain_core.tools import tool

from app.config import settings

logger = logging.getLogger(__name__)

_SESSION: requests.Session | None = None

_AV_BASE = "https://www.alphavantage.co/query"


# =============================================================================
# Shared HTTP session (used by yfinance and Alpha Vantage)
# =============================================================================

def _get_session() -> requests.Session:
    """
    Return a shared requests.Session pre-warmed with Yahoo Finance cookies.

    Yahoo Finance blocks bare automated requests from cloud IPs.
    Visiting the quote page first sets the necessary consent cookies that
    allow subsequent API calls (fast_info / history / download) to succeed.
    """
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://finance.yahoo.com/",
            "Origin": "https://finance.yahoo.com",
        })
        # Pre-warm: fetch the Yahoo Finance quote page to obtain cookies
        # (consent cookies, crumb, etc.) required by subsequent API calls
        try:
            resp = s.get("https://finance.yahoo.com/quote/AMZN", timeout=10)
            logger.info(f"Yahoo Finance session pre-warm: HTTP {resp.status_code}, "
                        f"cookies={list(s.cookies.keys())}")
        except Exception as e:
            logger.warning(f"Yahoo Finance session pre-warm failed: {e}")
        _SESSION = s
    return _SESSION


def _ticker(symbol: str) -> yf.Ticker:
    return yf.Ticker(symbol, session=_get_session())


def _retry(fn, retries: int = 3, delay: float = 2.0):
    """Call fn() up to `retries` times with `delay` seconds between attempts."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return fn(), None
        except Exception as e:
            last_exc = e
            logger.warning(f"Attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(delay)
    return None, last_exc


# =============================================================================
# Alpha Vantage helpers
# =============================================================================

def _av_get(params: dict) -> dict | None:
    """Make a GET request to Alpha Vantage. Returns parsed JSON or None on error."""
    api_key = settings.alpha_vantage_api_key
    if not api_key:
        logger.info("Alpha Vantage API key not configured — skipping")
        return None

    try:
        resp = _get_session().get(
            _AV_BASE,
            params={**params, "apikey": api_key},
            timeout=15
        )

        if resp.status_code != 200:
            logger.warning(f"Alpha Vantage HTTP error: {resp.status_code}")
            return None

        if not resp.text.strip():
            logger.warning("Alpha Vantage returned empty response body")
            return None

        try:
            data = resp.json()
        except ValueError:
            logger.warning(f"Alpha Vantage returned non-JSON response: {resp.text[:200]}")
            return None

        if "Information" in data:
            logger.warning(f"Alpha Vantage info: {data['Information']}")
            return None

        if "Error Message" in data:
            logger.warning(f"Alpha Vantage error: {data['Error Message']}")
            return None

        return data

    except requests.RequestException as e:
        logger.warning(f"Alpha Vantage request failed: {e}")
        return None


def _av_realtime_price(symbol: str) -> dict | None:
    """Fetch current price via Alpha Vantage GLOBAL_QUOTE endpoint."""
    data = _av_get({"function": "GLOBAL_QUOTE", "symbol": symbol})
    if not data:
        return None
    quote = data.get("Global Quote", {})
    price_str = quote.get("05. price")
    if not price_str:
        logger.warning(f"Alpha Vantage GLOBAL_QUOTE returned no price for {symbol}: {quote}")
        return None
    return {
        "ticker": symbol,
        "price": round(float(price_str), 2),
        "currency": "USD",
        "exchange": quote.get("07. latest trading day", ""),
    }


def _av_historical_prices(symbol: str, start_date: str, end_date: str) -> dict | None:
    """Fetch historical daily prices via Alpha Vantage TIME_SERIES_DAILY endpoint."""
    data = _av_get({"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": "full"})
    if not data:
        return None
    series = data.get("Time Series (Daily)", {})
    if not series:
        logger.warning(f"Alpha Vantage TIME_SERIES_DAILY returned empty series for {symbol}")
        return None

    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()

    records = []
    for date_str, values in sorted(series.items()):
        date = datetime.strptime(date_str, "%Y-%m-%d").date()
        if start <= date <= end:
            records.append({
                "date": date_str,
                "open": round(float(values["1. open"]), 2),
                "high": round(float(values["2. high"]), 2),
                "low": round(float(values["3. low"]), 2),
                "close": round(float(values["4. close"]), 2),
                "volume": int(values["5. volume"]),
            })

    if not records:
        logger.warning(f"Alpha Vantage: no data for {symbol} between {start_date} and {end_date}")
        return None

    return {
        "ticker": symbol,
        "start_date": start_date,
        "end_date": end_date,
        "count": len(records),
        "data": records,
    }


# =============================================================================
# LangChain tools
# =============================================================================

@tool
def retrieve_realtime_stock_price(
    ticker: Annotated[str, "Stock ticker symbol, e.g. 'AMZN'"]
) -> dict:
    """Retrieve the current real-time stock price for a given ticker symbol."""
    symbol = ticker.upper()

    # --- Strategy 1: Alpha Vantage (reliable from AWS) ---
    av_result = _av_realtime_price(symbol)
    if av_result:
        logger.info(f"retrieve_realtime_stock_price({symbol}): Alpha Vantage succeeded")
        return av_result

    # --- Strategies 2-4: yfinance fallback ---
    def _fetch():
        stock = _ticker(symbol)
        price = None

        # Strategy 2: fast_info (single lightweight request)
        fast = stock.fast_info
        price = getattr(fast, "last_price", None) or getattr(fast, "regular_market_price", None)
        logger.info(f"fast_info {symbol}: last_price={getattr(fast, 'last_price', 'N/A')}, "
                    f"regular_market_price={getattr(fast, 'regular_market_price', 'N/A')}")

        # Strategy 3: 1-minute history (most recent bar = live price)
        if not price:
            hist = stock.history(period="1d", interval="1m")
            logger.info(f"history(1d,1m) {symbol}: empty={hist.empty}, rows={len(hist)}")
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])

        # Strategy 4: yf.download — uses a different internal request path
        if not price:
            df = yf.download(
                symbol, period="1d", interval="1m",
                session=_get_session(), progress=False, auto_adjust=True
            )
            logger.info(f"download(1d,1m) {symbol}: empty={df.empty}, rows={len(df)}")
            if not df.empty:
                price = float(df["Close"].iloc[-1])

        if not price:
            raise ValueError(f"All strategies returned no price data for {symbol}. "
                             "Yahoo Finance may be blocking this AWS IP.")

        return {
            "ticker": symbol,
            "price": round(price, 2),
            "currency": getattr(fast, "currency", "USD"),
            "exchange": getattr(fast, "exchange", ""),
        }

    result, error = _retry(_fetch)
    if error:
        logger.error(f"retrieve_realtime_stock_price({symbol}) failed: {error}")
        return {"ticker": symbol, "error": str(error)}
    return result


@tool
def retrieve_historical_stock_price(
    ticker: Annotated[str, "Stock ticker symbol, e.g. 'AMZN'"],
    start_date: Annotated[str, "Start date in YYYY-MM-DD format"],
    end_date: Annotated[str, "End date in YYYY-MM-DD format"],
) -> dict:
    """Retrieve historical daily stock prices for a ticker between start_date and end_date."""
    symbol = ticker.upper()

    # --- Strategy 1: Alpha Vantage (reliable from AWS) ---
    av_result = _av_historical_prices(symbol, start_date, end_date)
    if av_result:
        logger.info(f"retrieve_historical_stock_price({symbol}): Alpha Vantage succeeded")
        return av_result

    # --- Strategy 2-3: yfinance fallback ---
    def _fetch():
        # Strategy 2: Ticker.history()
        stock = _ticker(symbol)
        hist = stock.history(start=start_date, end=end_date, interval="1d")
        logger.info(f"history({symbol}, {start_date}→{end_date}): empty={hist.empty}, rows={len(hist)}")

        # Strategy 3: yf.download() — different internal request path
        if hist.empty:
            hist = yf.download(
                symbol, start=start_date, end=end_date, interval="1d",
                session=_get_session(), progress=False, auto_adjust=True
            )
            logger.info(f"download({symbol}, {start_date}→{end_date}): empty={hist.empty}, rows={len(hist)}")

        if hist.empty:
            raise ValueError(f"No historical data for {symbol} between {start_date} and {end_date}.")

        # Normalise column access (download() may return MultiIndex columns)
        if hasattr(hist.columns, "get_level_values"):
            hist.columns = hist.columns.get_level_values(0)

        return {
            "ticker": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "count": len(hist),
            "data": [
                {
                    "date": str(idx.date()),
                    "open": round(float(row["Open"]), 2),
                    "high": round(float(row["High"]), 2),
                    "low": round(float(row["Low"]), 2),
                    "close": round(float(row["Close"]), 2),
                    "volume": int(row["Volume"]),
                }
                for idx, row in hist.iterrows()
            ],
        }

    result, error = _retry(_fetch)
    if error:
        logger.error(f"retrieve_historical_stock_price({symbol}) failed: {error}")
        return {"ticker": symbol, "start_date": start_date, "end_date": end_date, "error": str(error)}
    return result
