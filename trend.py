"""
indicators/trend.py
─────────────────────────────────────────────────────────────────────────────
Trend-following indicators:
  • Simple / Exponential Moving Averages
  • Golden/Death Cross detection
  • Bollinger Bands breakout
  • Higher-Highs / Higher-Lows structure
─────────────────────────────────────────────────────────────────────────────
"""
import pandas as pd
import numpy as np
import pandas_ta as ta

import config


def add_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds SMA and EMA columns in-place.
    Returns the modified DataFrame.
    """
    for p in config.MA_PERIODS:
        df[f"sma{p}"] = ta.sma(df["close"], length=p)
    for p in config.EMA_PERIODS:
        df[f"ema{p}"] = ta.ema(df["close"], length=p)
    return df


def add_bollinger_bands(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds BB columns: bb_upper, bb_mid, bb_lower, bb_pct (0-1 position).
    """
    bb = ta.bbands(df["close"], length=config.BB_PERIOD, std=config.BB_STD)
    if bb is not None:
        df["bb_upper"] = bb.iloc[:, 2]   # BBU
        df["bb_mid"]   = bb.iloc[:, 1]   # BBM
        df["bb_lower"] = bb.iloc[:, 0]   # BBL
        df["bb_pct"]   = (df["close"] - df["bb_lower"]) / (
            df["bb_upper"] - df["bb_lower"] + 1e-9
        )
    return df


# ── Signal extractors ─────────────────────────────────────────────────────────

def check_price_vs_ma(df: pd.DataFrame) -> dict[str, bool]:
    """Returns booleans: price above each SMA."""
    last = df.iloc[-1]
    result = {}
    for p in config.MA_PERIODS:
        col = f"sma{p}"
        if col in df.columns and not pd.isna(last[col]):
            result[f"price_above_ma{p}"] = last["close"] > last[col]
        else:
            result[f"price_above_ma{p}"] = False
    return result


def check_ma_alignment(df: pd.DataFrame) -> dict[str, bool]:
    """
    Checks:
      • MA20 > MA50
      • MA50 > MA200
      • Golden cross (MA50 crossed above MA200 within last 5 bars)
      • EMA9 > EMA21
    """
    res  = {}
    last = df.iloc[-1]

    def _safe(col):
        v = last.get(col, np.nan)
        return float(v) if not pd.isna(v) else np.nan

    sma20 = _safe("sma20"); sma50 = _safe("sma50"); sma200 = _safe("sma200")
    ema9  = _safe("ema9");  ema21 = _safe("ema21")

    res["ma20_above_ma50"]  = (sma20 > sma50)  if not np.isnan(sma20 + sma50)  else False
    res["ma50_above_ma200"] = (sma50 > sma200) if not np.isnan(sma50 + sma200) else False
    res["ema9_above_ema21"] = (ema9  > ema21)  if not np.isnan(ema9  + ema21)  else False

    # Golden cross: MA50 just crossed above MA200 in last 5 bars
    gc = False
    if "sma50" in df.columns and "sma200" in df.columns:
        tail    = df[["sma50", "sma200"]].dropna().tail(6)
        if len(tail) >= 2:
            # Check if MA50 was below MA200 N bars ago but is above now
            above_now  = tail["sma50"].iloc[-1] > tail["sma200"].iloc[-1]
            below_prev = any(
                tail["sma50"].iloc[i] < tail["sma200"].iloc[i]
                for i in range(len(tail) - 1)
            )
            gc = above_now and below_prev
    res["golden_cross_recent"] = gc

    return res


def check_bb_breakout(df: pd.DataFrame) -> dict[str, bool]:
    """Detect price closing above BB upper band."""
    if "bb_upper" not in df.columns:
        return {"bb_breakout_upper": False}
    last = df.iloc[-1]
    return {
        "bb_breakout_upper": last["close"] > last["bb_upper"]
        if not pd.isna(last.get("bb_upper", np.nan)) else False
    }


def check_higher_highs_lows(df: pd.DataFrame, lookback: int = 10) -> dict[str, bool]:
    """
    Simplified HH/HL check on the last `lookback` bars.
    True if the 3 most-recent swing highs are rising and
    the 3 most-recent swing lows are rising.
    """
    try:
        tail   = df["close"].tail(lookback).values
        highs  = tail[1::2]   # crude approximation
        lows   = tail[::2]
        hh = all(highs[i] < highs[i+1] for i in range(len(highs)-1)) if len(highs) >= 2 else False
        hl = all(lows[i]  < lows[i+1]  for i in range(len(lows)-1))  if len(lows)  >= 2 else False
        return {"higher_highs_lows": hh and hl}
    except Exception:
        return {"higher_highs_lows": False}
