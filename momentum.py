"""
indicators/momentum.py
─────────────────────────────────────────────────────────────────────────────
Momentum indicators:
  • RSI (bullish zone + upward slope)
  • MACD (bullish crossover, histogram expansion)
─────────────────────────────────────────────────────────────────────────────
"""
import pandas as pd
import numpy as np
import pandas_ta as ta

import config


def add_rsi(df: pd.DataFrame) -> pd.DataFrame:
    """Appends rsi column."""
    df["rsi"] = ta.rsi(df["close"], length=config.RSI_PERIOD)
    return df


def add_macd(df: pd.DataFrame) -> pd.DataFrame:
    """Appends macd, macd_signal, macd_hist columns."""
    macd = ta.macd(
        df["close"],
        fast=config.MACD_FAST,
        slow=config.MACD_SLOW,
        signal=config.MACD_SIGNAL,
    )
    if macd is not None:
        df["macd"]        = macd.iloc[:, 0]
        df["macd_hist"]   = macd.iloc[:, 1]
        df["macd_signal"] = macd.iloc[:, 2]
    return df


def add_atr(df: pd.DataFrame) -> pd.DataFrame:
    """Appends ATR for volatility / stop-loss estimation."""
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=config.ATR_PERIOD)
    return df


# ── Signal extractors ─────────────────────────────────────────────────────────

def check_rsi_bullish(df: pd.DataFrame) -> dict[str, bool]:
    """
    RSI is in the bullish zone (50-72) AND trending upward over last 3 bars.
    """
    if "rsi" not in df.columns:
        return {"rsi_bullish": False}

    tail = df["rsi"].dropna().tail(4)
    if len(tail) < 2:
        return {"rsi_bullish": False}

    last   = tail.iloc[-1]
    in_zone = config.RSI_BULLISH_MIN <= last <= config.RSI_BULLISH_MAX
    rising  = tail.iloc[-1] > tail.iloc[-2]   # last bar RSI up
    return {"rsi_bullish": bool(in_zone and rising)}


def check_macd(df: pd.DataFrame) -> dict[str, bool]:
    """
    macd_bullish_cross  : MACD line crossed above signal line within last 3 bars
    macd_histogram_expand: histogram is positive AND growing (momentum accelerating)
    """
    res = {"macd_bullish_cross": False, "macd_histogram_expand": False}

    if "macd" not in df.columns or "macd_signal" not in df.columns:
        return res

    sub = df[["macd", "macd_signal", "macd_hist"]].dropna().tail(5)
    if len(sub) < 3:
        return res

    # Bullish cross: MACD was below signal, now above
    cross = False
    for i in range(len(sub) - 1, 0, -1):
        if sub["macd"].iloc[i] > sub["macd_signal"].iloc[i] and \
           sub["macd"].iloc[i-1] <= sub["macd_signal"].iloc[i-1]:
            cross = True
            break
    res["macd_bullish_cross"] = cross

    # Histogram expanding (positive and getting bigger over last 2 bars)
    h = sub["macd_hist"]
    res["macd_histogram_expand"] = bool(
        h.iloc[-1] > 0 and h.iloc[-1] > h.iloc[-2]
    )
    return res
