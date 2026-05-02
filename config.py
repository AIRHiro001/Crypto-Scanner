"""
config.py
─────────────────────────────────────────────────────────────────────────────
Central configuration for Crypto Futures Momentum Scanner.
All tunable parameters live here — edit this file, nothing else.
─────────────────────────────────────────────────────────────────────────────
"""
import os
from dotenv import load_dotenv

load_dotenv()   # loads .env if present

# ── Exchange ──────────────────────────────────────────────────────────────────
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
USE_TESTNET        = os.getenv("USE_TESTNET", "true").lower() == "true"

EXCHANGE_CONFIG = {
    "testnet": {
        "base_url": "https://testnet.binancefuture.com",
        "ws_url":   "wss://stream.binancefuture.com/stream",
    },
    "mainnet": {
        "base_url": "https://fapi.binance.com",
        "ws_url":   "wss://fstream.binance.com/stream",
    },
}
ACTIVE_ENV = "testnet" if USE_TESTNET else "mainnet"
BASE_URL   = EXCHANGE_CONFIG[ACTIVE_ENV]["base_url"]
WS_URL     = EXCHANGE_CONFIG[ACTIVE_ENV]["ws_url"]

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Scanning ──────────────────────────────────────────────────────────────────
QUOTE_CURRENCY      = "USDT"          # only USDT-margined perpetuals
MIN_24H_VOLUME_USDT = 50_000_000      # $50M minimum — adjust for more/fewer pairs
TOP_N_PAIRS         = 150             # how many top-volume pairs to scan
SCAN_INTERVAL_SEC   = 300             # re-scan every 5 minutes

# Timeframes to analyse (primary → lowest priority)
TIMEFRAMES = ["1d", "4h", "1h", "30m"]
PRIMARY_TF = "4h"                     # main timeframe for scoring

# ── Indicators ────────────────────────────────────────────────────────────────
# Moving averages
MA_PERIODS   = [20, 50, 200]
EMA_PERIODS  = [9, 21]

# RSI
RSI_PERIOD   = 14
RSI_BULLISH_MIN = 50   # not overbought but trending up
RSI_BULLISH_MAX = 72   # cap before overbought

# MACD
MACD_FAST    = 12
MACD_SLOW    = 26
MACD_SIGNAL  = 9

# Bollinger Bands
BB_PERIOD    = 20
BB_STD       = 2.0

# Volume spike
VOLUME_SPIKE_MULTIPLIER = 2.5   # volume > 2.5× its 20-bar average = spike
VOLUME_MA_PERIOD        = 20

# ATR (for volatility / SL estimates)
ATR_PERIOD   = 14

# ── Scoring / Ranking ─────────────────────────────────────────────────────────
# Each condition adds points → final score normalised to 0-100
SCORE_WEIGHTS = {
    "price_above_ma20":       5,
    "price_above_ma50":       8,
    "price_above_ma200":      10,
    "ma20_above_ma50":        7,
    "ma50_above_ma200":       10,
    "golden_cross_recent":    15,   # MA50 crossed above MA200 within last 5 bars
    "ema9_above_ema21":       5,
    "rsi_bullish":            10,
    "macd_bullish_cross":     12,
    "macd_histogram_expand":  8,
    "volume_spike":           10,
    "bb_breakout_upper":      10,
    "higher_highs_lows":      8,
    "multi_tf_alignment":     12,   # same signal on ≥2 timeframes
}
SIGNAL_MIN_SCORE   = 45    # minimum score to show in results
SIGNAL_HIGH_SCORE  = 70    # threshold for Telegram alert

# ── Backtesting ───────────────────────────────────────────────────────────────
BT_INITIAL_CAPITAL = 10_000   # USDT
BT_COMMISSION      = 0.0004   # 0.04% per trade (Binance futures taker)
BT_LOOKBACK_DAYS   = 180      # how far back to fetch data for backtest

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR   = "logs"
LOG_LEVEL = "INFO"
LOG_ROTATION = "1 day"
LOG_RETENTION = "7 days"

# ── Cache ─────────────────────────────────────────────────────────────────────
CACHE_DIR          = "cache"
CACHE_TTL_SECONDS  = 60   # OHLCV cache TTL per (symbol, tf)
