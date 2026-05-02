"""
backtest/engine.py
─────────────────────────────────────────────────────────────────────────────
Simple vectorised backtester built on top of the project's own indicator
stack (no extra backtesting library required).

Strategy tested:
  • Entry  : score >= SIGNAL_MIN_SCORE on the current bar
  • Exit   : after `hold_bars` bars  OR  if price drops > stop_loss_pct
  • Commission: BT_COMMISSION per trade (round-trip)

Returns a BacktestResult dataclass with all stats + equity curve DataFrame.
─────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import numpy as np

import config
from data_fetcher.binance_rest import fetch_historical_ohlcv
from indicators import (
    add_moving_averages, add_bollinger_bands,
    add_rsi, add_macd, add_atr, add_volume_indicators,
)
from scanner.signal_engine import _extract_signals, _score
from utils.logger import log


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    symbol:          str
    timeframe:       str
    total_trades:    int       = 0
    winning_trades:  int       = 0
    losing_trades:   int       = 0
    win_rate:        float     = 0.0
    avg_return_pct:  float     = 0.0
    total_return_pct:float     = 0.0
    max_drawdown_pct:float     = 0.0
    sharpe_ratio:    float     = 0.0
    profit_factor:   float     = 0.0
    final_capital:   float     = config.BT_INITIAL_CAPITAL
    equity_curve:    pd.DataFrame = field(default_factory=pd.DataFrame)
    trades:          pd.DataFrame = field(default_factory=pd.DataFrame)
    error:           Optional[str] = None

    def summary(self) -> dict:
        return {
            "Symbol":          self.symbol,
            "Timeframe":       self.timeframe,
            "Trades":          self.total_trades,
            "Win Rate":        f"{self.win_rate:.1f}%",
            "Avg Return":      f"{self.avg_return_pct:.2f}%",
            "Total Return":    f"{self.total_return_pct:.2f}%",
            "Max Drawdown":    f"{self.max_drawdown_pct:.2f}%",
            "Sharpe":          f"{self.sharpe_ratio:.2f}",
            "Profit Factor":   f"{self.profit_factor:.2f}",
            "Final Capital":   f"${self.final_capital:,.2f}",
        }


# ── Indicator pipeline (same as live scanner) ─────────────────────────────────

def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = add_moving_averages(df)
    df = add_bollinger_bands(df)
    df = add_rsi(df)
    df = add_macd(df)
    df = add_atr(df)
    df = add_volume_indicators(df)
    return df


# ── Core backtest function ────────────────────────────────────────────────────

def run_backtest(
    symbol:         str,
    timeframe:      str  = "4h",
    days:           int  = config.BT_LOOKBACK_DAYS,
    min_score:      float = config.SIGNAL_MIN_SCORE,
    hold_bars:      int  = 10,      # ~2.5 days on 4h
    stop_loss_pct:  float = 0.05,   # 5% stop loss
    take_profit_pct:float = 0.12,   # 12% take profit
) -> BacktestResult:
    """
    Vectorised walk-forward backtest.

    Args:
        symbol          : e.g. "BTC/USDT:USDT"
        timeframe       : "4h", "1h", "1d" …
        days            : lookback period in days
        min_score       : minimum signal score to enter
        hold_bars       : max bars to hold a position
        stop_loss_pct   : exit if loss > this fraction
        take_profit_pct : exit at this gain
    """
    log.info(f"Backtesting {symbol} [{timeframe}] over {days} days …")

    # ── 1. Fetch data ─────────────────────────────────────────────────────────
    df = fetch_historical_ohlcv(symbol, timeframe, days)
    if df is None or len(df) < 100:
        return BacktestResult(symbol=symbol, timeframe=timeframe,
                              error="Not enough historical data")

    # ── 2. Enrich with indicators ─────────────────────────────────────────────
    df = _enrich(df)

    # ── 3. Pre-compute signal scores per bar (rolling window approach) ────────
    min_window = max(config.MA_PERIODS)   # 200 bars warmup
    scores     = np.full(len(df), 0.0)

    for i in range(min_window, len(df)):
        window = df.iloc[:i + 1]
        try:
            sigs  = _extract_signals(window)
            sc    = _score(sigs, config.SCORE_WEIGHTS)
            scores[i] = sc
        except Exception:
            scores[i] = 0.0

    df["bt_score"] = scores

    # ── 4. Simulate trades ────────────────────────────────────────────────────
    capital    = config.BT_INITIAL_CAPITAL
    in_trade   = False
    entry_price = 0.0
    entry_bar   = 0
    trades_log  = []
    equity      = [capital]
    commission  = config.BT_COMMISSION

    closes = df["close"].values
    n      = len(closes)

    for i in range(min_window, n):
        if not in_trade:
            # Entry condition
            if scores[i] >= min_score:
                entry_price = closes[i] * (1 + commission)   # include slippage
                entry_bar   = i
                in_trade    = True
        else:
            bars_held = i - entry_bar
            ret       = (closes[i] - entry_price) / entry_price

            exit_reason = None
            if ret <= -stop_loss_pct:
                exit_reason = "stop_loss"
            elif ret >= take_profit_pct:
                exit_reason = "take_profit"
            elif bars_held >= hold_bars:
                exit_reason = "time_exit"

            if exit_reason:
                exit_price = closes[i] * (1 - commission)
                net_ret    = (exit_price - entry_price) / entry_price
                pnl        = capital * net_ret
                capital   += pnl
                trades_log.append({
                    "entry_bar":   entry_bar,
                    "exit_bar":    i,
                    "entry_price": entry_price,
                    "exit_price":  exit_price,
                    "return_pct":  round(net_ret * 100, 3),
                    "pnl":         round(pnl, 2),
                    "exit_reason": exit_reason,
                    "bars_held":   bars_held,
                    "score":       scores[entry_bar],
                    "timestamp":   df.index[i],
                })
                in_trade = False

        equity.append(capital)

    # Force-close any open trade at end
    if in_trade and n > entry_bar:
        exit_price = closes[-1] * (1 - commission)
        net_ret    = (exit_price - entry_price) / entry_price
        pnl        = capital * net_ret
        capital   += pnl
        trades_log.append({
            "entry_bar":   entry_bar,
            "exit_bar":    n - 1,
            "entry_price": entry_price,
            "exit_price":  exit_price,
            "return_pct":  round(net_ret * 100, 3),
            "pnl":         round(pnl, 2),
            "exit_reason": "end_of_data",
            "bars_held":   n - 1 - entry_bar,
            "score":       scores[entry_bar],
            "timestamp":   df.index[-1],
        })

    # ── 5. Build equity curve ─────────────────────────────────────────────────
    eq_index  = df.index[min_window - 1:]
    eq_series = pd.DataFrame(
        {"equity": equity[:len(eq_index)]},
        index=eq_index
    )

    trades_df = pd.DataFrame(trades_log) if trades_log else pd.DataFrame()

    # ── 6. Compute stats ──────────────────────────────────────────────────────
    if not trades_log:
        return BacktestResult(
            symbol=symbol, timeframe=timeframe,
            equity_curve=eq_series, trades=trades_df,
            error="No trades triggered — lower min_score or extend lookback"
        )

    rets       = [t["return_pct"] for t in trades_log]
    wins       = [r for r in rets if r > 0]
    losses     = [r for r in rets if r <= 0]

    win_rate   = len(wins) / len(rets) * 100

    # Sharpe (daily-approx using trade returns)
    rets_arr   = np.array(rets)
    sharpe     = (rets_arr.mean() / (rets_arr.std() + 1e-9)) * np.sqrt(252)

    # Profit factor
    gross_win  = sum(wins)     if wins   else 0
    gross_loss = abs(sum(losses)) if losses else 1e-9
    pf         = gross_win / gross_loss

    # Max drawdown on equity curve
    eq_vals    = eq_series["equity"].values
    peak       = np.maximum.accumulate(eq_vals)
    drawdowns  = (eq_vals - peak) / peak * 100
    max_dd     = float(drawdowns.min())

    total_ret  = (capital - config.BT_INITIAL_CAPITAL) / config.BT_INITIAL_CAPITAL * 100

    result = BacktestResult(
        symbol           = symbol,
        timeframe        = timeframe,
        total_trades     = len(trades_log),
        winning_trades   = len(wins),
        losing_trades    = len(losses),
        win_rate         = round(win_rate, 1),
        avg_return_pct   = round(float(np.mean(rets)), 2),
        total_return_pct = round(total_ret, 2),
        max_drawdown_pct = round(max_dd, 2),
        sharpe_ratio     = round(float(sharpe), 2),
        profit_factor    = round(pf, 2),
        final_capital    = round(capital, 2),
        equity_curve     = eq_series,
        trades           = trades_df,
    )

    log.success(
        f"Backtest done: {symbol} | {len(trades_log)} trades | "
        f"WR={win_rate:.1f}% | Return={total_ret:.1f}% | DD={max_dd:.1f}%"
    )
    return result
