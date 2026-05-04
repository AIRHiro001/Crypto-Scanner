"""
alerts/telegram.py
─────────────────────────────────────────────────────────────────────────────
Telegram alert module using python-telegram-bot (v21, async).

Features:
  • send_signal_alert()   — rich message for a single SignalResult
  • send_scan_summary()   — digest of top-N signals after each scan
  • Rate-limited: max 1 alert per symbol per 30 minutes (dedup cache)
  • Graceful no-op when TELEGRAM_TOKEN / CHAT_ID not configured
─────────────────────────────────────────────────────────────────────────────
"""
import asyncio
import time
from typing import TYPE_CHECKING

import config
from logger import log

if TYPE_CHECKING:
    from scanner.signal_engine import SignalResult


# ── Dedup cache: symbol → last_sent_ts ────────────────────────────────────────
_alert_cache: dict[str, float] = {}
ALERT_COOLDOWN_SEC = 1800   # 30 minutes per symbol


def _is_configured() -> bool:
    return bool(config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID)


def _cooldown_ok(symbol: str) -> bool:
    last = _alert_cache.get(symbol, 0)
    return (time.time() - last) > ALERT_COOLDOWN_SEC


def _mark_sent(symbol: str):
    _alert_cache[symbol] = time.time()


# ── Message formatter ─────────────────────────────────────────────────────────

def _format_signal(result: "SignalResult") -> str:
    """Build a nicely formatted Telegram HTML message."""
    # Score bar (10 blocks)
    filled = round(result.score / 10)
    bar    = "█" * filled + "░" * (10 - filled)

    # Change lines
    changes = ""
    for tf, pct in result.change_pct.items():
        arrow  = "📈" if pct >= 0 else "📉"
        changes += f"  {arrow} <b>{tf.upper()}</b>: {pct:+.2f}%\n"

    reasons_txt   = "\n".join(f"  • {r}" for r in result.reasons)
    risks_txt     = "\n".join(f"  ⚠️ {r}" for r in result.risk_factors) or "  None detected"
    env_label     = "🧪 TESTNET" if config.USE_TESTNET else "🔴 MAINNET"

    return (
        f"🚀 <b>MOMENTUM SIGNAL</b>  [{env_label}]\n"
        f"{'─'*34}\n"
        f"📌 <b>{result.symbol}</b>  @  <code>${result.price:,.4f}</code>\n\n"
        f"🎯 <b>Score:</b> <code>{result.score:.0f}/100</code>  {result.confidence}\n"
        f"<code>[{bar}]</code>\n\n"
        f"📊 <b>Price Change:</b>\n{changes}\n"
        f"💹 <b>Est. Upside:</b>  <code>+{result.upside_est:.1f}%</code>\n"
        f"📦 <b>24h Volume:</b>  ${result.volume_24h/1e6:.1f}M\n"
        f"📐 <b>ATR:</b>  {result.atr:.4f}\n\n"
        f"✅ <b>Signal Reasons:</b>\n{reasons_txt}\n\n"
        f"🛡 <b>Risk Factors:</b>\n{risks_txt}\n\n"
        f"🕐 <i>{result.timestamp}</i>"
    )


def _format_summary(results: list["SignalResult"], top_n: int = 5) -> str:
    top = results[:top_n]
    lines = [f"📋 <b>SCAN SUMMARY</b> — Top {len(top)} Signals\n{'─'*30}"]
    for i, r in enumerate(top, 1):
        lines.append(
            f"{i}. <b>{r.symbol}</b>  "
            f"Score: <code>{r.score:.0f}</code>  "
            f"{r.confidence}  "
            f"Est: <code>+{r.upside_est:.1f}%</code>"
        )
    lines.append(f"\n<i>Total qualified: {len(results)} pairs scanned</i>")
    return "\n".join(lines)


# ── Async send helpers ────────────────────────────────────────────────────────

async def _async_send(text: str):
    try:
        from telegram import Bot
        bot = Bot(token=config.TELEGRAM_TOKEN)
        await bot.send_message(
            chat_id    = config.TELEGRAM_CHAT_ID,
            text       = text,
            parse_mode = "HTML",
        )
        log.debug("Telegram message sent ✓")
    except Exception as e:
        log.error(f"Telegram send error: {e}")


def _send(text: str):
    """Sync wrapper — runs async send in a fresh event loop."""
    try:
        asyncio.run(_async_send(text))
    except RuntimeError:
        # Already inside a running loop (e.g. Streamlit) — use thread
        import threading
        threading.Thread(
            target=lambda: asyncio.run(_async_send(text)),
            daemon=True,
        ).start()


# ── Public API ────────────────────────────────────────────────────────────────

def send_signal_alert(result: "SignalResult"):
    """
    Send a Telegram alert for a single high-confidence signal.
    Respects cooldown — won't spam the same symbol.
    """
    if not _is_configured():
        log.debug("Telegram not configured — skipping alert")
        return
    if result.score < config.SIGNAL_HIGH_SCORE:
        return
    if not _cooldown_ok(result.symbol):
        log.debug(f"Telegram cooldown active for {result.symbol}")
        return

    msg = _format_signal(result)
    _send(msg)
    _mark_sent(result.symbol)
    log.info(f"📨 Telegram alert sent: {result.symbol} (score={result.score})")


def send_scan_summary(results: list["SignalResult"], top_n: int = 5):
    """Send a brief digest of the top-N signals from a completed scan."""
    if not _is_configured() or not results:
        return
    msg = _format_summary(results, top_n)
    _send(msg)
    log.info("📨 Telegram scan summary sent")


def send_raw(text: str):
    """Send arbitrary plain text — useful for startup / error notifications."""
    if not _is_configured():
        return
    _send(text)
