"""
indicators/volume.py
─────────────────────────────────────────────────────────────────────────────
Volume-based indicators:
  • Volume Moving Average
  • Volume spike detection (current vs N-bar average)
  • On-Balance Volume trend (OBV slope)
─────────────────────────────────────────────────────────────────────────────
"""
import pandas as pd
import numpy as np
import pandas_ta as ta

import config


def add_volume_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Appends:
      • vol_sma   : rolling mean of volume
      • vol_ratio : volume / vol_sma  (>2.5 = spike)
      • obv       : on-balance volume
    """
    df["vol_sma"]   = df["volume"].rolling(config.VOLUME_MA_PERIOD).mean()
    df["vol_ratio"] = df["volume"] / (df["vol_sma"] + 1e-9)
    df["obv"]       = ta.obv(df["close"], df["volume"])
    return df


# ── Signal extractors ─────────────────────────────────────────────────────────

def check_volume_spike(df: pd.DataFrame) -> dict[str, bool | float]:
    """
    Returns:
      volume_spike  (bool)  : latest bar volume > VOLUME_SPIKE_MULTIPLIER × average
      vol_ratio     (float) : actual ratio for display
    """
    if "vol_ratio" not in df.columns:
        return {"volume_spike": False, "vol_ratio": 1.0}

    ratio = df["vol_ratio"].iloc[-1]
    if pd.isna(ratio):
        return {"volume_spike": False, "vol_ratio": 1.0}

    return {
        "volume_spike": ratio >= config.VOLUME_SPIKE_MULTIPLIER,
        "vol_ratio":    round(float(ratio), 2),
    }


def check_obv_trend(df: pd.DataFrame, lookback: int = 10) -> dict[str, bool]:
    """OBV trending upward (linear regression slope > 0) over last N bars."""
    if "obv" not in df.columns:
        return {"obv_rising": False}
    tail = df["obv"].dropna().tail(lookback).values
    if len(tail) < 5:
        return {"obv_rising": False}
    x    = np.arange(len(tail))
    slope = np.polyfit(x, tail, 1)[0]
    return {"obv_rising": slope > 0}
