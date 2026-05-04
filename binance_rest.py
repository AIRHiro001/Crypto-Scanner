"""
data_fetcher/binance_rest.py
─────────────────────────────────────────────────────────────────────────────
Binance Futures REST client built on top of ccxt.
Handles:
  • symbol discovery + liquidity filter
  • OHLCV fetching (with in-memory TTL cache)
  • 24-h ticker snapshot
  • Retry / back-off via tenacity
  • Rate-limit awareness
─────────────────────────────────────────────────────────────────────────────
"""
import time
import os
import json
from typing import Optional
import pandas as pd
import ccxt
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type
)

import config
from logger import log
from rate_limiter import rate_limiter


# ── Exchange singleton ────────────────────────────────────────────────────────

def _build_exchange() -> ccxt.binanceusdm:
    params = {
        "apiKey":  config.BINANCE_API_KEY,
        "secret":  config.BINANCE_API_SECRET,
        "enableRateLimit": False,   # we manage this ourselves
        "options": {"defaultType": "future"},
    }
    if config.USE_TESTNET:
        params["urls"] = {
            "api": {
                "fapiPublic":  "https://testnet.binancefuture.com/fapi/v1",
                "fapiPrivate": "https://testnet.binancefuture.com/fapi/v1",
                "fapiPublicV2": "https://testnet.binancefuture.com/fapi/v2",
            }
        }
    exchange = ccxt.binanceusdm(params)
    log.info(f"Exchange initialised — {'TESTNET' if config.USE_TESTNET else 'MAINNET'}")
    return exchange


_exchange: Optional[ccxt.binanceusdm] = None

def get_exchange() -> ccxt.binanceusdm:
    global _exchange
    if _exchange is None:
        _exchange = _build_exchange()
    return _exchange


# ── OHLCV in-memory cache ─────────────────────────────────────────────────────

_ohlcv_cache: dict[str, dict] = {}   # key = "SYMBOL_TF"

def _cache_key(symbol: str, tf: str) -> str:
    return f"{symbol}_{tf}"

def _cache_get(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    key   = _cache_key(symbol, tf)
    entry = _ohlcv_cache.get(key)
    if entry and time.time() - entry["ts"] < config.CACHE_TTL_SECONDS:
        return entry["df"]
    return None

def _cache_set(symbol: str, tf: str, df: pd.DataFrame):
    _ohlcv_cache[_cache_key(symbol, tf)] = {"df": df, "ts": time.time()}


# ── Symbol discovery ──────────────────────────────────────────────────────────

def get_top_symbols(n: int = config.TOP_N_PAIRS) -> list[str]:
    """
    Return top-N USDT perpetual futures symbols ranked by 24-h volume.
    Applies minimum volume filter from config.
    """
    ex = get_exchange()
    try:
        rate_limiter.acquire(10)
        tickers = ex.fetch_tickers()
    except Exception as e:
        log.error(f"Failed to fetch tickers: {e}")
        return []

    rows = []
    for symbol, t in tickers.items():
        # Only USDT-margined perpetuals
        if not symbol.endswith(f"/{config.QUOTE_CURRENCY}:{config.QUOTE_CURRENCY}"):
            continue
        quoteVol = t.get("quoteVolume") or 0
        if quoteVol < config.MIN_24H_VOLUME_USDT:
            continue
        rows.append({"symbol": symbol, "volume": quoteVol})

    df = pd.DataFrame(rows).sort_values("volume", ascending=False).head(n)
    symbols = df["symbol"].tolist()
    log.info(f"Discovered {len(symbols)} qualified pairs (top {n} by volume)")
    return symbols


# ── OHLCV fetch ───────────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((ccxt.NetworkError, ccxt.RequestTimeout)),
    reraise=True,
)
def fetch_ohlcv(
    symbol: str,
    timeframe: str,
    limit: int = 300,
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    Fetch OHLCV candles and return as a tidy DataFrame.
    Columns: open, high, low, close, volume  (index = UTC datetime)
    """
    if use_cache:
        cached = _cache_get(symbol, timeframe)
        if cached is not None:
            return cached

    ex = get_exchange()
    rate_limiter.acquire(2)

    raw = ex.fetch_ohlcv(symbol, timeframe, limit=limit)
    df  = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.set_index("ts", inplace=True)
    df = df.astype(float)

    _cache_set(symbol, timeframe, df)
    return df


# ── 24-h ticker snapshot ──────────────────────────────────────────────────────

def fetch_ticker_snapshot(symbols: list[str]) -> dict[str, dict]:
    """
    Return {symbol: ticker_dict} for a list of symbols.
    Uses batch fetch for efficiency.
    """
    ex = get_exchange()
    result = {}
    try:
        rate_limiter.acquire(10)
        tickers = ex.fetch_tickers(symbols)
        for s, t in tickers.items():
            result[s] = {
                "price":         t.get("last")         or 0,
                "change_pct_1d": t.get("percentage")   or 0,
                "volume_24h":    t.get("quoteVolume")  or 0,
                "high_24h":      t.get("high")         or 0,
                "low_24h":       t.get("low")          or 0,
                "bid":           t.get("bid")          or 0,
                "ask":           t.get("ask")          or 0,
            }
    except Exception as e:
        log.error(f"fetch_ticker_snapshot error: {e}")
    return result


# ── Historical data for backtesting ──────────────────────────────────────────

def fetch_historical_ohlcv(
    symbol: str,
    timeframe: str = "4h",
    days: int = config.BT_LOOKBACK_DAYS,
) -> pd.DataFrame:
    """
    Fetch a long history of OHLCV data by paginating Binance's 1 500-bar limit.
    Returns a single concatenated DataFrame.
    """
    ex  = get_exchange()
    tf_ms = {
        "30m": 30 * 60 * 1000,
        "1h":  60 * 60 * 1000,
        "4h":  4  * 60 * 60 * 1000,
        "1d":  24 * 60 * 60 * 1000,
    }
    ms_per_bar = tf_ms.get(timeframe, 60 * 60 * 1000)
    total_bars = (days * 24 * 60 * 60 * 1000) // ms_per_bar
    batch_size = 1000
    all_bars   = []
    since      = ex.milliseconds() - days * 24 * 60 * 60 * 1000

    while len(all_bars) < total_bars:
        rate_limiter.acquire(2)
        try:
            batch = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=batch_size)
        except Exception as e:
            log.warning(f"Pagination error for {symbol}: {e}")
            break
        if not batch:
            break
        all_bars += batch
        since     = batch[-1][0] + 1
        if len(batch) < batch_size:
            break

    if not all_bars:
        return pd.DataFrame()

    df = pd.DataFrame(all_bars, columns=["ts", "open", "high", "low", "close", "volume"])
    df.drop_duplicates("ts", inplace=True)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df.set_index("ts", inplace=True)
    df = df.astype(float).sort_index()
    log.info(f"Fetched {len(df)} bars for {symbol} [{timeframe}]")
    return df
