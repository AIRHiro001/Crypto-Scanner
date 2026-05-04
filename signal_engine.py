"""
scanner/signal_engine.py
─────────────────────────────────────────────────────────────────────────────
Main signal engine:
  1. For each symbol, fetch OHLCV on all configured timeframes
  2. Apply all indicators
  3. Extract signals per timeframe
  4. Score & rank
  5. Return a list of SignalResult dataclass objects
─────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations
import concurrent.futures
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import numpy as np

import config
from data_fetcher.binance_rest import fetch_ohlcv, fetch_ticker_snapshot
from indicators import (
    add_moving_averages, add_bollinger_bands,
    check_price_vs_ma, check_ma_alignment,
    check_bb_breakout, check_higher_highs_lows,
    add_rsi, add_macd, add_atr,
    check_rsi_bullish, check_macd,
    add_volume_indicators, check_volume_spike, check_obv_trend,
)
from logger import log


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class SignalResult:
    symbol:         str
    price:          float
    score:          float           # 0-100
    confidence:     str             # Low / Medium / High / Very High
    reasons:        list[str]       = field(default_factory=list)
    risk_factors:   list[str]       = field(default_factory=list)
    change_pct:     dict[str, float] = field(default_factory=dict)   # tf → %
    upside_est:     float           = 0.0    # % estimated upside
    atr:            float           = 0.0
    volume_24h:     float           = 0.0
    vol_ratio:      float           = 1.0
    tf_signals:     dict[str, dict] = field(default_factory=dict)    # tf → raw bools
    timestamp:      str             = ""

    def to_dict(self) -> dict:
        return {
            "Symbol":       self.symbol,
            "Price":        self.price,
            "Score":        self.score,
            "Confidence":   self.confidence,
            "Upside Est":   f"+{self.upside_est:.1f}%",
            "24h Vol ($M)": round(self.volume_24h / 1e6, 1),
            "Vol Ratio":    self.vol_ratio,
            "ATR":          self.atr,
            "Reasons":      " | ".join(self.reasons),
            "Risks":        " | ".join(self.risk_factors),
            "Timestamp":    self.timestamp,
        }


# ── Indicator pipeline ────────────────────────────────────────────────────────

def _enrich_df(df: pd.DataFrame) -> pd.DataFrame:
    """Apply all indicators to a raw OHLCV DataFrame."""
    df = add_moving_averages(df)
    df = add_bollinger_bands(df)
    df = add_rsi(df)
    df = add_macd(df)
    df = add_atr(df)
    df = add_volume_indicators(df)
    return df


def _extract_signals(df: pd.DataFrame) -> dict[str, bool | float]:
    """Extract all signal booleans from an enriched DataFrame."""
    sigs = {}
    sigs.update(check_price_vs_ma(df))
    sigs.update(check_ma_alignment(df))
    sigs.update(check_bb_breakout(df))
    sigs.update(check_higher_highs_lows(df))
    sigs.update(check_rsi_bullish(df))
    sigs.update(check_macd(df))
    vol = check_volume_spike(df)
    sigs.update(vol)
    sigs.update(check_obv_trend(df))
    return sigs


def _score(signals: dict[str, bool | float], weights: dict[str, int]) -> float:
    """Sum weights for True conditions, normalise to 0-100."""
    total_possible = sum(weights.values())
    earned = sum(w for k, w in weights.items() if signals.get(k) is True)
    return round(min(earned / total_possible * 100, 100), 1)


def _confidence_label(score: float) -> str:
    if score >= 80:  return "🔥 Very High"
    if score >= 65:  return "✅ High"
    if score >= 50:  return "🟡 Medium"
    return "🔵 Low"


def _build_reasons(signals: dict, tf: str) -> list[str]:
    reasons = []
    if signals.get("golden_cross_recent"):         reasons.append(f"[{tf}] Golden Cross (MA50×MA200)")
    if signals.get("ma20_above_ma50"):             reasons.append(f"[{tf}] MA20 > MA50 aligned")
    if signals.get("ma50_above_ma200"):            reasons.append(f"[{tf}] MA50 > MA200 bullish")
    if signals.get("price_above_ma200"):           reasons.append(f"[{tf}] Price above MA200")
    if signals.get("rsi_bullish"):                 reasons.append(f"[{tf}] RSI bullish zone (50-72)")
    if signals.get("macd_bullish_cross"):          reasons.append(f"[{tf}] MACD bullish crossover")
    if signals.get("macd_histogram_expand"):       reasons.append(f"[{tf}] MACD histogram expanding")
    if signals.get("volume_spike"):
        ratio = signals.get("vol_ratio", 0)
        reasons.append(f"[{tf}] Volume spike {ratio:.1f}× avg")
    if signals.get("bb_breakout_upper"):           reasons.append(f"[{tf}] BB upper breakout")
    if signals.get("higher_highs_lows"):           reasons.append(f"[{tf}] Higher highs & higher lows")
    if signals.get("obv_rising"):                  reasons.append(f"[{tf}] OBV trending up")
    return reasons


def _build_risks(df: pd.DataFrame, signals: dict, ticker: dict) -> list[str]:
    risks = []
    # Volatility
    atr = df["atr"].dropna().iloc[-1] if "atr" in df.columns else 0
    price = df["close"].iloc[-1]
    atr_pct = (atr / price * 100) if price else 0
    if atr_pct > 5:
        risks.append(f"High volatility (ATR {atr_pct:.1f}% of price)")

    # RSI near overbought
    if "rsi" in df.columns:
        rsi = df["rsi"].dropna().iloc[-1]
        if rsi > 68:
            risks.append(f"RSI near overbought ({rsi:.0f})")

    # Low 24h volume
    vol = ticker.get("volume_24h", 0)
    if 0 < vol < config.MIN_24H_VOLUME_USDT * 2:
        risks.append(f"Liquidity borderline (${vol/1e6:.0f}M)")

    # Wide spread
    bid = ticker.get("bid", 0); ask = ticker.get("ask", 0)
    if bid and ask:
        spread_pct = (ask - bid) / bid * 100
        if spread_pct > 0.1:
            risks.append(f"Wide spread ({spread_pct:.2f}%)")

    return risks


def _estimate_upside(df: pd.DataFrame, atr: float) -> float:
    """
    Conservative upside estimate: 2× ATR as a realistic near-term target.
    Caps at 25% to stay realistic.
    """
    price = df["close"].iloc[-1]
    if price <= 0: return 0
    est = min((2 * atr / price) * 100, 25)
    return round(est, 1)


# ── Single symbol analysis ────────────────────────────────────────────────────

def analyse_symbol(symbol: str, ticker: dict) -> Optional[SignalResult]:
    """
    Run full analysis on one symbol across all timeframes.
    Returns None if symbol fails to fetch data or score < MIN.
    """
    from datetime import datetime, timezone
    tf_signals: dict[str, dict] = {}
    combined_signals: dict[str, bool | float] = {}
    all_reasons: list[str] = []
    atr_val = 0.0

    for tf in config.TIMEFRAMES:
        try:
            df = fetch_ohlcv(symbol, tf, limit=300)
            if df is None or len(df) < 50:
                continue
            df = _enrich_df(df)
            sigs = _extract_signals(df)
            tf_signals[tf] = sigs

            # Collect reasons from primary + secondary TFs
            all_reasons += _build_reasons(sigs, tf)

            # Merge: if ANY timeframe fires a signal, count it
            for k, v in sigs.items():
                if v is True:
                    combined_signals[k] = True
                elif k not in combined_signals:
                    combined_signals[k] = v

            # ATR from primary TF
            if tf == config.PRIMARY_TF and "atr" in df.columns:
                a = df["atr"].dropna()
                atr_val = float(a.iloc[-1]) if len(a) else 0
                upside  = _estimate_upside(df, atr_val)

        except Exception as e:
            log.warning(f"  {symbol} [{tf}]: {e}")
            continue

    if not combined_signals:
        return None

    # Multi-TF alignment bonus
    tf_with_signals = sum(
        1 for tf, sigs in tf_signals.items()
        if sum(1 for v in sigs.values() if v is True) >= 3
    )
    if tf_with_signals >= 2:
        combined_signals["multi_tf_alignment"] = True

    score = _score(combined_signals, config.SCORE_WEIGHTS)
    if score < config.SIGNAL_MIN_SCORE:
        return None

    # Deduplicate reasons, keep top 5
    seen = set(); unique_reasons = []
    for r in all_reasons:
        key = r.split("] ", 1)[-1] if "] " in r else r
        if key not in seen:
            seen.add(key); unique_reasons.append(r)

    risks = _build_risks(df, combined_signals, ticker)

    # Percentage change per TF (close_0 vs close_-1)
    change_pct = {}
    for tf, sigs in tf_signals.items():
        try:
            d = fetch_ohlcv(symbol, tf, limit=5, use_cache=True)
            if d is not None and len(d) >= 2:
                c = float(d["close"].iloc[-1])
                p = float(d["close"].iloc[-2])
                change_pct[tf] = round((c - p) / p * 100, 2) if p else 0
        except Exception:
            pass

    price   = ticker.get("price", 0)
    vol_24h = ticker.get("volume_24h", 0)

    return SignalResult(
        symbol       = symbol,
        price        = price,
        score        = score,
        confidence   = _confidence_label(score),
        reasons      = unique_reasons[:6],
        risk_factors = risks,
        change_pct   = change_pct,
        upside_est   = upside,
        atr          = round(atr_val, 4),
        volume_24h   = vol_24h,
        vol_ratio    = combined_signals.get("vol_ratio", 1.0),
        tf_signals   = tf_signals,
        timestamp    = datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    )


# ── Batch scanner ─────────────────────────────────────────────────────────────

def run_scan(symbols: list[str], max_workers: int = 8) -> list[SignalResult]:
    """
    Scan all symbols concurrently.
    Returns list sorted by score descending.
    """
    log.info(f"Starting scan: {len(symbols)} symbols, {max_workers} workers")

    # Batch fetch tickers first
    tickers = fetch_ticker_snapshot(symbols)

    results: list[SignalResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(analyse_symbol, sym, tickers.get(sym, {})): sym
            for sym in symbols
        }
        for fut in concurrent.futures.as_completed(futures):
            sym = futures[fut]
            try:
                res = fut.result()
                if res:
                    results.append(res)
                    log.debug(f"  ✓ {sym:25s}  score={res.score:5.1f}  {res.confidence}")
            except Exception as e:
                log.warning(f"  ✗ {sym}: {e}")

    results.sort(key=lambda r: r.score, reverse=True)
    log.success(f"Scan complete: {len(results)} signals found (threshold={config.SIGNAL_MIN_SCORE})")
    return results
