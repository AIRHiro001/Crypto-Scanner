"""
ui/charts.py
─────────────────────────────────────────────────────────────────────────────
Plotly chart factory functions consumed by the Streamlit dashboard.
─────────────────────────────────────────────────────────────────────────────
"""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ── Colour palette ─────────────────────────────────────────────────────────────
BG      = "#0d1117"
GRID    = "#21262d"
TEXT    = "#e6edf3"
GREEN   = "#3fb950"
RED     = "#f85149"
BLUE    = "#58a6ff"
ORANGE  = "#d29922"
PURPLE  = "#bc8cff"
YELLOW  = "#e3b341"


def _base_layout(title: str = "") -> dict:
    return dict(
        title       = dict(text=title, font=dict(color=TEXT, size=14)),
        paper_bgcolor = BG,
        plot_bgcolor  = BG,
        font          = dict(color=TEXT),
        xaxis         = dict(gridcolor=GRID, showgrid=True, zeroline=False),
        yaxis         = dict(gridcolor=GRID, showgrid=True, zeroline=False),
        margin        = dict(l=10, r=10, t=40, b=10),
        legend        = dict(bgcolor=BG, bordercolor=GRID),
        hovermode     = "x unified",
    )


# ── Candlestick + MAs + Volume ────────────────────────────────────────────────

def candlestick_chart(df: pd.DataFrame, symbol: str, tf: str) -> go.Figure:
    """
    Main chart: candlesticks, MA20/50/200, volume bars, BB bands.
    Uses a 3-row subplot (price | volume | RSI).
    """
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.60, 0.20, 0.20],
        vertical_spacing=0.02,
        subplot_titles=("", "Volume", "RSI"),
    )

    # ── Row 1: candlesticks ───────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"],
        low=df["low"], close=df["close"],
        name="Price",
        increasing_line_color=GREEN, decreasing_line_color=RED,
        increasing_fillcolor=GREEN,  decreasing_fillcolor=RED,
    ), row=1, col=1)

    # MAs
    ma_styles = [
        ("sma20",  BLUE,   "MA20"),
        ("sma50",  ORANGE, "MA50"),
        ("sma200", PURPLE, "MA200"),
    ]
    for col, color, name in ma_styles:
        if col in df.columns:
            fig.add_trace(go.Scatter(
                x=df.index, y=df[col], name=name,
                line=dict(color=color, width=1.5),
                opacity=0.8,
            ), row=1, col=1)

    # Bollinger Bands (filled area)
    if "bb_upper" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["bb_upper"], name="BB Upper",
            line=dict(color=YELLOW, width=1, dash="dot"),
            opacity=0.6,
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=df.index, y=df["bb_lower"], name="BB Lower",
            line=dict(color=YELLOW, width=1, dash="dot"),
            fill="tonexty", fillcolor="rgba(227,179,65,0.05)",
            opacity=0.6,
        ), row=1, col=1)

    # ── Row 2: volume ─────────────────────────────────────────────────────────
    colors = [
        GREEN if df["close"].iloc[i] >= df["open"].iloc[i] else RED
        for i in range(len(df))
    ]
    fig.add_trace(go.Bar(
        x=df.index, y=df["volume"], name="Volume",
        marker_color=colors, opacity=0.7,
        showlegend=False,
    ), row=2, col=1)

    if "vol_sma" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["vol_sma"], name="Vol MA20",
            line=dict(color=ORANGE, width=1.5),
        ), row=2, col=1)

    # ── Row 3: RSI ────────────────────────────────────────────────────────────
    if "rsi" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["rsi"], name="RSI",
            line=dict(color=BLUE, width=1.5),
        ), row=3, col=1)
        # Overbought / oversold zones
        fig.add_hline(y=70, line_color=RED,   line_dash="dash", opacity=0.5, row=3, col=1)
        fig.add_hline(y=30, line_color=GREEN, line_dash="dash", opacity=0.5, row=3, col=1)
        fig.add_hline(y=50, line_color=GRID,  line_dash="dash", opacity=0.4, row=3, col=1)

    # ── Layout ────────────────────────────────────────────────────────────────
    layout = _base_layout(f"{symbol}  [{tf.upper()}]")
    layout.update(
        height=600,
        xaxis_rangeslider_visible=False,
        yaxis=dict(title="Price", gridcolor=GRID, zeroline=False),
        yaxis2=dict(title="Vol",  gridcolor=GRID, zeroline=False),
        yaxis3=dict(title="RSI",  gridcolor=GRID, zeroline=False,
                    range=[0, 100]),
    )
    fig.update_layout(**layout)
    return fig


# ── MACD chart ─────────────────────────────────────────────────────────────────

def macd_chart(df: pd.DataFrame, symbol: str) -> go.Figure:
    if "macd" not in df.columns:
        return go.Figure()

    hist_colors = [GREEN if v >= 0 else RED for v in df["macd_hist"].fillna(0)]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df.index, y=df["macd_hist"], name="Histogram",
        marker_color=hist_colors, opacity=0.8,
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=df["macd"], name="MACD",
        line=dict(color=BLUE, width=1.5),
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=df["macd_signal"], name="Signal",
        line=dict(color=ORANGE, width=1.5),
    ))
    fig.add_hline(y=0, line_color=GRID, line_dash="dash", opacity=0.5)
    fig.update_layout(**_base_layout(f"{symbol} — MACD"), height=280)
    return fig


# ── Equity curve (backtest) ───────────────────────────────────────────────────

def equity_curve_chart(equity_df: pd.DataFrame, trades_df: pd.DataFrame,
                       symbol: str) -> go.Figure:
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=equity_df.index, y=equity_df["equity"],
        name="Portfolio Value",
        line=dict(color=BLUE, width=2),
        fill="tozeroy", fillcolor="rgba(88,166,255,0.08)",
    ))

    if not trades_df.empty and "timestamp" in trades_df.columns:
        wins  = trades_df[trades_df["return_pct"] > 0]
        loses = trades_df[trades_df["return_pct"] <= 0]

        if not wins.empty:
            fig.add_trace(go.Scatter(
                x=wins["timestamp"], y=[equity_df["equity"].iloc[0]] * len(wins),
                mode="markers", name="Win", marker=dict(color=GREEN, size=8, symbol="triangle-up"),
            ))
        if not loses.empty:
            fig.add_trace(go.Scatter(
                x=loses["timestamp"], y=[equity_df["equity"].iloc[0]] * len(loses),
                mode="markers", name="Loss", marker=dict(color=RED, size=8, symbol="triangle-down"),
            ))

    fig.add_hline(
        y=10_000, line_color=GRID, line_dash="dash",
        annotation_text="Initial Capital",
        annotation_font_color=TEXT,
    )
    fig.update_layout(**_base_layout(f"Equity Curve — {symbol}"), height=350)
    return fig


# ── Score distribution bar chart ──────────────────────────────────────────────

def score_bar_chart(results: list) -> go.Figure:
    if not results:
        return go.Figure()

    symbols = [r.symbol.split("/")[0] for r in results[:20]]
    scores  = [r.score for r in results[:20]]
    colors  = [
        GREEN  if s >= 70
        else ORANGE if s >= 55
        else BLUE
        for s in scores
    ]

    fig = go.Figure(go.Bar(
        x=symbols, y=scores,
        marker_color=colors,
        text=[f"{s:.0f}" for s in scores],
        textposition="outside",
    ))
    fig.update_layout(
        **_base_layout("Signal Scores — Top 20"),
        height=320,
        yaxis=dict(range=[0, 105], gridcolor=GRID),
    )
    return fig
