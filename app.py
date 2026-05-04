import sys
from pathlib import Path
# ==================== FIX PATH UNTUK STREAMLIT CLOUD ====================
root_path = str(Path(__file__).parent)
sys.path.append(root_path)
# ============================================================

import time
import threading
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import streamlit as st

# ── Page config (HARUS PALING ATAS) ────────────────────────────────
st.set_page_config(
    page_title="Crypto Momentum Scanner",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ====================== IMPORTS YANG DIPERBAIKI ======================
import config

# Import langsung dari root (ini yang paling penting)
from binance_rest import get_top_symbols, fetch_ohlcv, fetch_ticker_snapshot
from websocket_client import BinanceWebSocket, price_store
from signal_engine import run_scan, SignalResult
from telegram import send_signal_alert, send_scan_summary
from charts import (
    candlestick_chart, macd_chart,
    equity_curve_chart, score_bar_chart,
)

# Logger
from logger import log

# Indicators (dengan safety)
try:
    from indicators import (
        add_moving_averages, add_bollinger_bands,
        add_rsi, add_macd, add_atr, add_volume_indicators,
    )
except ImportError:
    add_moving_averages = add_bollinger_bands = add_rsi = add_macd = add_atr = add_volume_indicators = lambda x: x
# =====================================================================


# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Dark theme overrides */
:root { --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #e6edf3; --muted: #8b949e; }
.main { background-color: var(--bg); }

/* Metric cards */
div[data-testid="metric-container"] {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 16px;
}

/* Signal table rows */
.high-score    { background: rgba(63,185,80,0.08)  !important; }
.medium-score  { background: rgba(210,153,34,0.08) !important; }

/* Score badge */
.score-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    font-weight: 700;
    font-size: 13px;
}
.badge-green  { background:#1a4731; color:#3fb950; }
.badge-orange { background:#3d2b0a; color:#d29922; }
.badge-blue   { background:#0c2a43; color:#58a6ff; }

/* Sidebar */
section[data-testid="stSidebar"] { background: var(--card); }

/* Tabs */
.stTabs [data-baseweb="tab"] { color: var(--muted); }
.stTabs [aria-selected="true"] { color: var(--text) !important; }

/* Scrollable table */
.signals-table { max-height: 480px; overflow-y: auto; }
</style>
""", unsafe_allow_html=True)


# ── Session state initialisation ──────────────────────────────────────────────
def _init_state():
    defaults = {
        "symbols":        [],
        "scan_results":   [],
        "last_scan_ts":   None,
        "ws":             None,
        "ws_started":     False,
        "scan_running":   False,
        "selected_symbol": None,
        "auto_refresh":   False,
        "refresh_interval": 300,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ── Helpers ───────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def _cached_top_symbols():
    return get_top_symbols()


def _start_websocket(symbols: list[str]):
    if st.session_state.ws_started or not symbols:
        return
    ws = BinanceWebSocket(symbols)
    ws.start()
    st.session_state.ws         = ws
    st.session_state.ws_started = True


def _do_scan(symbols: list[str]) -> list[SignalResult]:
    st.session_state.scan_running = True
    results = run_scan(symbols, max_workers=6)
    st.session_state.scan_results  = results
    st.session_state.last_scan_ts  = datetime.now(timezone.utc)
    st.session_state.scan_running  = False

    # Send Telegram alerts for high-confidence signals
    for r in results:
        send_signal_alert(r)
    send_scan_summary(results, top_n=5)
    return results


def _get_enriched_df(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    """Fetch + enrich OHLCV for the chart panel."""
    try:
        df = fetch_ohlcv(symbol, tf, limit=300)
        if df is None or df.empty:
            return None
        df = add_moving_averages(df)
        df = add_bollinger_bands(df)
        df = add_rsi(df)
        df = add_macd(df)
        df = add_atr(df)
        df = add_volume_indicators(df)
        return df
    except Exception as e:
        log.warning(f"_get_enriched_df {symbol} [{tf}]: {e}")
        return None


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ⚙️ Scanner Config")

    env_badge = "🧪 **TESTNET**" if config.USE_TESTNET else "🔴 **MAINNET**"
    st.markdown(env_badge)
    st.divider()

    st.markdown("### 🔍 Filters")
    top_n = st.slider("Top N pairs by volume", 20, 200, config.TOP_N_PAIRS, 10)
    min_vol = st.number_input(
        "Min 24h Vol ($M)", min_value=10, max_value=500,
        value=int(config.MIN_24H_VOLUME_USDT / 1e6), step=10
    )
    min_score_filter = st.slider(
        "Min Signal Score (display)", 0, 100, config.SIGNAL_MIN_SCORE, 5
    )

    st.divider()
    st.markdown("### 🕐 Timeframes")
    tf_options = ["30m", "1h", "4h", "1d"]
    selected_tfs = st.multiselect(
        "Active timeframes", tf_options, default=config.TIMEFRAMES
    )
    primary_tf = st.selectbox("Primary TF (chart)", tf_options, index=2)

    st.divider()
    st.markdown("### 🔄 Auto Refresh")
    auto_refresh = st.toggle("Enable auto-refresh", value=False)
    if auto_refresh:
        refresh_sec = st.slider("Interval (seconds)", 60, 900, 300, 60)
        st.session_state.auto_refresh     = True
        st.session_state.refresh_interval = refresh_sec
    else:
        st.session_state.auto_refresh = False

    st.divider()
    st.markdown("### 📨 Alerts")
    tg_ok = bool(config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID)
    if tg_ok:
        st.success("Telegram ✅ configured")
    else:
        st.warning("Telegram not configured\n\nSet TELEGRAM_TOKEN & TELEGRAM_CHAT_ID in .env")

    st.divider()
    st.caption(
        "Crypto Futures Momentum Scanner\n"
        "Built with Streamlit + ccxt + pandas-ta\n"
        "⚠️ Not financial advice"
    )


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown(
    "<h1 style='margin-bottom:0'>🚀 Crypto Futures Momentum Scanner</h1>"
    "<p style='color:#8b949e;margin-top:4px'>Real-time signal detection for Binance USDT perpetuals</p>",
    unsafe_allow_html=True
)

env_color = "#d29922" if config.USE_TESTNET else "#f85149"
st.markdown(
    f"<span style='background:{env_color}22;color:{env_color};"
    f"padding:4px 12px;border-radius:8px;font-size:13px;font-weight:700'>"
    f"{'🧪 TESTNET MODE' if config.USE_TESTNET else '🔴 MAINNET — REAL MONEY'}</span>",
    unsafe_allow_html=True
)
st.markdown("")


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_scanner, tab_chart, tab_backtest, tab_live, tab_help = st.tabs([
    "📊 Scanner",
    "📈 Charts",
    "🔬 Backtest",
    "⚡ Live Prices",
    "❓ Help",
])


# ════════════════════════════════════════════════════════════════════════════════
# TAB 1 — SCANNER
# ════════════════════════════════════════════════════════════════════════════════
with tab_scanner:
    col_btn1, col_btn2, col_info = st.columns([1, 1, 3])

    with col_btn1:
        scan_btn = st.button(
            "🔍 Run Scan",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.scan_running,
        )
    with col_btn2:
        load_syms_btn = st.button(
            "♻️ Reload Symbols",
            use_container_width=True,
        )

    with col_info:
        if st.session_state.last_scan_ts:
            st.markdown(
                f"<small>Last scan: **{st.session_state.last_scan_ts.strftime('%H:%M:%S UTC')}** "
                f"| Found **{len(st.session_state.scan_results)}** signals</small>",
                unsafe_allow_html=True,
            )

    # Load / reload symbols
    if load_syms_btn or not st.session_state.symbols:
        with st.spinner("Fetching top symbols from Binance…"):
            # Temporarily override config values from sidebar
            config.TOP_N_PAIRS          = top_n
            config.MIN_24H_VOLUME_USDT  = min_vol * 1_000_000
            syms = _cached_top_symbols.clear() or get_top_symbols()
            st.session_state.symbols = syms
            _start_websocket(syms)
        st.success(f"Loaded **{len(syms)}** symbols")

    # Trigger scan
    if scan_btn and st.session_state.symbols:
        with st.spinner(f"Scanning {len(st.session_state.symbols)} pairs across {len(selected_tfs)} timeframes…"):
            config.TIMEFRAMES = selected_tfs
            config.PRIMARY_TF = primary_tf
            _do_scan(st.session_state.symbols)
        st.rerun()

    results: list[SignalResult] = st.session_state.scan_results

    if not results:
        st.info("Click **Run Scan** to start. First scan may take 1-2 minutes.")
    else:
        # ── Summary KPIs ──────────────────────────────────────────────────────
        total   = len(results)
        hi      = sum(1 for r in results if r.score >= 70)
        med     = sum(1 for r in results if 55 <= r.score < 70)
        avg_sc  = sum(r.score for r in results) / total if total else 0
        top_sym = results[0].symbol.split("/")[0] if results else "—"

        kc1, kc2, kc3, kc4, kc5 = st.columns(5)
        kc1.metric("Signals Found",  total)
        kc2.metric("🔥 Very High / High", hi)
        kc3.metric("🟡 Medium",  med)
        kc4.metric("Avg Score",  f"{avg_sc:.1f}")
        kc5.metric("Top Pick",   top_sym)

        st.divider()

        # ── Filter controls ───────────────────────────────────────────────────
        fc1, fc2, fc3 = st.columns([2, 2, 2])
        with fc1:
            conf_filter = st.multiselect(
                "Confidence filter",
                ["🔥 Very High", "✅ High", "🟡 Medium", "🔵 Low"],
                default=["🔥 Very High", "✅ High", "🟡 Medium"],
            )
        with fc2:
            sort_by = st.selectbox("Sort by", ["Score", "Volume 24h", "Upside Est"])
        with fc3:
            show_top = st.slider("Show top N", 5, 50, 20)

        # Apply filters
        filtered = [
            r for r in results
            if r.confidence in conf_filter
            and r.score >= min_score_filter
        ]
        if sort_by == "Volume 24h":
            filtered.sort(key=lambda r: r.volume_24h, reverse=True)
        elif sort_by == "Upside Est":
            filtered.sort(key=lambda r: r.upside_est, reverse=True)

        filtered = filtered[:show_top]

        # ── Signal table ──────────────────────────────────────────────────────
        if not filtered:
            st.warning("No signals match current filters.")
        else:
            # Build display DataFrame
            rows = []
            for r in filtered:
                rows.append({
                    "Symbol":       r.symbol.split("/")[0],
                    "Price":        f"${r.price:,.4f}" if r.price < 1000 else f"${r.price:,.2f}",
                    "Score":        r.score,
                    "Confidence":   r.confidence,
                    "Upside Est":   f"+{r.upside_est:.1f}%",
                    "24h Vol ($M)": f"{r.volume_24h/1e6:.0f}M",
                    "Vol Ratio":    f"{r.vol_ratio:.1f}×",
                    "ATR":          f"{r.atr:.4f}",
                    "Top Reason":   r.reasons[0] if r.reasons else "—",
                    "Risk":         r.risk_factors[0] if r.risk_factors else "✅ None",
                    "Time":         r.timestamp,
                })
            tbl_df = pd.DataFrame(rows)

            def _style_score(val):
                if val >= 70: return "background-color:#1a4731;color:#3fb950;font-weight:700"
                if val >= 55: return "background-color:#3d2b0a;color:#d29922;font-weight:700"
                return "background-color:#0c2a43;color:#58a6ff"

            styled = (
                tbl_df.style
                .applymap(_style_score, subset=["Score"])
                .set_properties(**{"font-size": "13px"})
            )
            st.dataframe(styled, use_container_width=True, height=420)

            # ── Per-signal expanders ──────────────────────────────────────────
            st.markdown("### 🔎 Signal Details")
            for r in filtered[:10]:
                conf_emoji = r.confidence.split()[0]
                with st.expander(
                    f"{conf_emoji} **{r.symbol.split('/')[0]}**  —  "
                    f"Score: **{r.score:.0f}**  |  "
                    f"Est. Upside: **+{r.upside_est:.1f}%**"
                ):
                    d1, d2, d3 = st.columns(3)
                    d1.metric("Current Price",  f"${r.price:,.4f}" if r.price < 1000 else f"${r.price:,.2f}")
                    d2.metric("24h Volume",      f"${r.volume_24h/1e6:.0f}M")
                    d3.metric("Vol Spike Ratio", f"{r.vol_ratio:.1f}×")

                    # Change per TF
                    if r.change_pct:
                        st.markdown("**Price Change by Timeframe:**")
                        tf_cols = st.columns(len(r.change_pct))
                        for i, (tf, pct) in enumerate(r.change_pct.items()):
                            arrow = "📈" if pct >= 0 else "📉"
                            tf_cols[i].metric(tf.upper(), f"{pct:+.2f}%", delta=f"{pct:+.2f}%")

                    # Reasons
                    st.markdown("**✅ Signal Reasons:**")
                    for reason in r.reasons:
                        st.markdown(f"&nbsp;&nbsp;• {reason}")

                    # Risks
                    if r.risk_factors:
                        st.markdown("**⚠️ Risk Factors:**")
                        for risk in r.risk_factors:
                            st.markdown(f"&nbsp;&nbsp;⚠️ {risk}")

                    # Action buttons
                    acol1, acol2 = st.columns(2)
                    if acol1.button(f"📈 View Chart", key=f"chart_{r.symbol}"):
                        st.session_state.selected_symbol = r.symbol
                        st.rerun()
                    if acol2.button(f"🔬 Backtest", key=f"bt_{r.symbol}"):
                        st.session_state.bt_symbol = r.symbol
                        st.rerun()

    # ── Auto-refresh countdown ────────────────────────────────────────────────
    if st.session_state.auto_refresh and st.session_state.last_scan_ts:
        elapsed = (datetime.now(timezone.utc) - st.session_state.last_scan_ts).seconds
        remaining = max(0, st.session_state.refresh_interval - elapsed)
        st.markdown(
            f"<small style='color:#8b949e'>Auto-refresh in **{remaining}s**</small>",
            unsafe_allow_html=True
        )
        if remaining == 0 and st.session_state.symbols:
            _do_scan(st.session_state.symbols)
            st.rerun()
        time.sleep(1)
        st.rerun()


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — CHARTS
# ════════════════════════════════════════════════════════════════════════════════
with tab_chart:
    st.markdown("### 📈 Technical Chart")

    # Symbol picker
    all_syms = st.session_state.symbols or []
    result_syms = [r.symbol for r in st.session_state.scan_results]
    display_syms = result_syms + [s for s in all_syms if s not in result_syms]

    pre_select = 0
    if st.session_state.get("selected_symbol") in display_syms:
        pre_select = display_syms.index(st.session_state.selected_symbol)

    cc1, cc2 = st.columns([3, 1])
    with cc1:
        chosen_sym = st.selectbox(
            "Select Symbol",
            display_syms or ["BTC/USDT:USDT"],
            index=pre_select,
            format_func=lambda s: s.split("/")[0],
        )
    with cc2:
        chosen_tf = st.selectbox(
            "Timeframe", ["30m", "1h", "4h", "1d"], index=2,
        )

    if chosen_sym:
        with st.spinner(f"Loading {chosen_sym} [{chosen_tf}]…"):
            chart_df = _get_enriched_df(chosen_sym, chosen_tf)

        if chart_df is not None:
            # Latest values row
            last = chart_df.iloc[-1]
            lv1, lv2, lv3, lv4, lv5 = st.columns(5)
            close = float(last["close"])
            prev  = float(chart_df.iloc[-2]["close"]) if len(chart_df) > 1 else close
            chg   = (close - prev) / prev * 100
            lv1.metric("Price",    f"${close:,.4f}" if close < 1000 else f"${close:,.2f}", f"{chg:+.2f}%")
            lv2.metric("RSI",      f"{last.get('rsi', 0):.1f}" if "rsi" in chart_df.columns else "—")
            lv3.metric("MA50",     f"{last.get('sma50', 0):.4f}" if "sma50" in chart_df.columns else "—")
            lv4.metric("Vol Ratio",f"{last.get('vol_ratio', 0):.1f}×" if "vol_ratio" in chart_df.columns else "—")
            lv5.metric("ATR",      f"{last.get('atr', 0):.4f}" if "atr" in chart_df.columns else "—")

            # Main candlestick chart
            st.plotly_chart(
                candlestick_chart(chart_df.tail(200), chosen_sym, chosen_tf),
                use_container_width=True,
            )

            # MACD chart
            if "macd" in chart_df.columns:
                st.plotly_chart(
                    macd_chart(chart_df.tail(200), chosen_sym),
                    use_container_width=True,
                )
        else:
            st.error(f"Could not load data for {chosen_sym}. Check connection.")

    # Score distribution chart (if scan done)
    if st.session_state.scan_results:
        st.divider()
        st.markdown("### 🏆 Signal Score Leaderboard")
        st.plotly_chart(
            score_bar_chart(st.session_state.scan_results),
            use_container_width=True,
        )


# ════════════════════════════════════════════════════════════════════════════════
# TAB 3 — BACKTEST
# ════════════════════════════════════════════════════════════════════════════════
with tab_backtest:
    st.markdown("### 🔬 Strategy Backtester")
    st.info(
        "Tests the **Momentum Score** strategy on historical data. "
        "Entry when score ≥ threshold; exit at take-profit, stop-loss, or time limit."
    )

    all_syms_bt = st.session_state.symbols or ["BTC/USDT:USDT"]
    pre_bt = 0
    if st.session_state.get("bt_symbol") in all_syms_bt:
        pre_bt = all_syms_bt.index(st.session_state.bt_symbol)

    bc1, bc2, bc3 = st.columns(3)
    with bc1:
        bt_sym = st.selectbox(
            "Symbol", all_syms_bt, index=pre_bt,
            format_func=lambda s: s.split("/")[0],
            key="bt_sym_sel",
        )
    with bc2:
        bt_tf = st.selectbox("Timeframe", ["4h", "1h", "1d"], index=0, key="bt_tf_sel")
    with bc3:
        bt_days = st.slider("Lookback (days)", 30, 365, 180, key="bt_days_sl")

    bc4, bc5, bc6, bc7 = st.columns(4)
    with bc4:
        bt_min_score = st.slider("Min Score", 30, 90, int(config.SIGNAL_MIN_SCORE), key="bt_sc_sl")
    with bc5:
        bt_hold = st.slider("Hold Bars", 3, 30, 10, key="bt_hold_sl")
    with bc6:
        bt_sl = st.slider("Stop Loss %", 2, 20, 5, key="bt_sl_sl") / 100
    with bc7:
        bt_tp = st.slider("Take Profit %", 5, 50, 12, key="bt_tp_sl") / 100

    run_bt_btn = st.button("▶ Run Backtest", type="primary", key="run_bt_btn")

    if run_bt_btn and bt_sym:
        from backtest.engine import run_backtest
        with st.spinner(f"Backtesting {bt_sym} [{bt_tf}] over {bt_days} days…"):
            bt_result = run_backtest(
                symbol          = bt_sym,
                timeframe       = bt_tf,
                days            = bt_days,
                min_score       = bt_min_score,
                hold_bars       = bt_hold,
                stop_loss_pct   = bt_sl,
                take_profit_pct = bt_tp,
            )
        st.session_state["bt_result"] = bt_result

    if "bt_result" in st.session_state:
        r = st.session_state["bt_result"]

        if r.error:
            st.warning(f"⚠️ {r.error}")
        else:
            # KPI metrics
            rc1, rc2, rc3, rc4, rc5, rc6 = st.columns(6)
            delta_color = "normal" if r.total_return_pct >= 0 else "inverse"
            rc1.metric("Total Return",   f"{r.total_return_pct:.1f}%", delta=f"{r.total_return_pct:.1f}%")
            rc2.metric("Win Rate",       f"{r.win_rate:.1f}%")
            rc3.metric("Total Trades",   r.total_trades)
            rc4.metric("Profit Factor",  f"{r.profit_factor:.2f}")
            rc5.metric("Max Drawdown",   f"{r.max_drawdown_pct:.1f}%")
            rc6.metric("Sharpe Ratio",   f"{r.sharpe_ratio:.2f}")

            # Final capital
            pnl = r.final_capital - config.BT_INITIAL_CAPITAL
            pnl_color = "#3fb950" if pnl >= 0 else "#f85149"
            st.markdown(
                f"<h4 style='color:{pnl_color}'>Final Capital: ${r.final_capital:,.2f} "
                f"({'+'if pnl>=0 else ''}{pnl:,.2f} USD)</h4>",
                unsafe_allow_html=True
            )

            # Equity curve
            if not r.equity_curve.empty:
                st.plotly_chart(
                    equity_curve_chart(r.equity_curve, r.trades, r.symbol),
                    use_container_width=True,
                )

            # Trade log
            if not r.trades.empty:
                with st.expander("📋 Trade Log"):
                    display_cols = [
                        c for c in ["timestamp", "entry_price", "exit_price",
                                    "return_pct", "pnl", "bars_held",
                                    "exit_reason", "score"]
                        if c in r.trades.columns
                    ]
                    st.dataframe(r.trades[display_cols], use_container_width=True)

            # Disclaimer
            st.caption(
                "⚠️ Past performance does not guarantee future results. "
                "Backtest does not account for slippage beyond commission, "
                "funding rates, or market impact."
            )


# ════════════════════════════════════════════════════════════════════════════════
# TAB 4 — LIVE PRICES
# ════════════════════════════════════════════════════════════════════════════════
with tab_live:
    st.markdown("### ⚡ Live WebSocket Prices")

    if not st.session_state.ws_started:
        st.warning("Load symbols first (Scanner tab → ♻️ Reload Symbols) to start WebSocket.")
    else:
        ws_data = price_store.all()
        if ws_data:
            live_rows = []
            for sym, d in list(ws_data.items())[:50]:
                age = time.time() - d.get("ts", 0)
                live_rows.append({
                    "Symbol":   sym,
                    "Price":    f"${d['price']:,.4f}" if d['price'] < 1000 else f"${d['price']:,.2f}",
                    "High":     f"${d['high']:,.4f}"  if d['high']  < 1000 else f"${d['high']:,.2f}",
                    "Low":      f"${d['low']:,.4f}"   if d['low']   < 1000 else f"${d['low']:,.2f}",
                    "Vol (Q)":  f"${d['quote_vol']/1e6:.1f}M",
                    "Age (s)":  f"{age:.0f}s",
                })
            st.dataframe(pd.DataFrame(live_rows), use_container_width=True, height=500)
            st.caption(f"Showing {len(live_rows)} live feeds. Updates every ~1s.")

            lp_col1, lp_col2 = st.columns(2)
            if lp_col1.button("🔄 Refresh Live Data"):
                st.rerun()
        else:
            st.info("Waiting for WebSocket data… (may take 5-10 seconds after connect)")
            time.sleep(2)
            st.rerun()


# ════════════════════════════════════════════════════════════════════════════════
# TAB 5 — HELP
# ════════════════════════════════════════════════════════════════════════════════
with tab_help:
    st.markdown("""
## 📖 How to Use This Scanner

### 1. Setup
1. Copy `.env.example` → `.env`
2. Add your Binance **Testnet** API keys
3. Optionally add Telegram bot token + chat ID
4. Run: `streamlit run app.py`

### 2. Running a Scan
- Click **♻️ Reload Symbols** to fetch the top liquid pairs
- Click **🔍 Run Scan** to analyse all pairs
- Results are ranked by **Signal Score (0-100)**
- Each signal shows technical reasons + estimated upside

### 3. Understanding Scores

| Score | Confidence  | Meaning                          |
|-------|-------------|----------------------------------|
| 70+   | 🔥 Very High | Multiple strong confluences      |
| 55-70 | ✅ High      | Clear momentum setup             |
| 45-55 | 🟡 Medium   | Some signals, watch closely      |
| <45   | 🔵 Low       | Weak — filtered out by default   |

### 4. Indicators Used
| Indicator       | Signal Condition                                   |
|-----------------|----------------------------------------------------|
| **MA 20/50/200**| Price above MAs; MA20 > MA50 > MA200 alignment     |
| **Golden Cross**| MA50 crossed above MA200 within last 5 bars        |
| **EMA 9/21**    | EMA9 > EMA21 short-term momentum                   |
| **RSI**         | Between 50-72 and rising (not overbought)          |
| **MACD**        | Bullish crossover + histogram expanding            |
| **Volume**      | Current bar > 2.5× 20-bar average                 |
| **Bollinger**   | Price closes above upper band (breakout)           |
| **HH/HL**       | Series of higher highs + higher lows               |
| **Multi-TF**    | Same setup confirmed on ≥2 timeframes (bonus pts)  |

### 5. Backtest Settings Guide
- **Hold Bars**: 10 bars on 4h ≈ 40h hold time
- **Stop Loss**: 5% is conservative for crypto futures
- **Take Profit**: 12% realistic for momentum swings
- **Min Score**: Lower = more trades but lower quality

### 6. Telegram Alerts
Alerts fire when:
- Signal score ≥ `SIGNAL_HIGH_SCORE` (default: 70)
- Same symbol not alerted in last 30 minutes

### 7. Deployed on Streamlit Cloud?
- Testnet REST API works fine ✅
- WebSocket may be blocked on some free tiers ⚠️
  (app will fall back to REST polling automatically)

---
⚠️ **Disclaimer**: This tool is for educational purposes only. 
Crypto trading carries significant risk. Never invest money you cannot afford to lose.
Past backtest performance does not predict future results.
    """)

# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "🚀 Crypto Futures Momentum Scanner  •  "
    f"{'Testnet' if config.USE_TESTNET else 'Mainnet'}  •  "
    "Built with Streamlit · ccxt · pandas-ta · plotly  •  "
    "⚠️ Not financial advice"
)
