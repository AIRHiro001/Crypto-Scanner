"""
data_fetcher/websocket_client.py
─────────────────────────────────────────────────────────────────────────────
Async WebSocket client for Binance Futures.
Subscribes to miniTicker streams for a batch of symbols and stores the
latest price/volume in a shared in-memory dict.

Usage (run in a background thread):
    ws = BinanceWebSocket(symbols)
    ws.start()          # non-blocking — spawns a background thread
    price = ws.get_price("BTCUSDT")
    ws.stop()
─────────────────────────────────────────────────────────────────────────────
"""
import asyncio
import json
import threading
import time
from typing import Optional

import websockets

import config
from logger import log


# ── Shared state (thread-safe via lock) ───────────────────────────────────────

class _PriceStore:
    def __init__(self):
        self._data: dict[str, dict] = {}
        self._lock = threading.Lock()

    def update(self, symbol: str, payload: dict):
        with self._lock:
            self._data[symbol] = payload

    def get(self, symbol: str) -> Optional[dict]:
        with self._lock:
            return self._data.get(symbol)

    def all(self) -> dict:
        with self._lock:
            return dict(self._data)


price_store = _PriceStore()


# ── WebSocket client ──────────────────────────────────────────────────────────

class BinanceWebSocket:
    """
    Subscribes to Binance Futures miniTicker streams.
    Automatically reconnects on disconnect (exponential back-off).
    """
    MAX_SYMBOLS_PER_CONNECTION = 200   # Binance hard limit per connection

    def __init__(self, symbols: list[str]):
        # Convert ccxt "BTC/USDT:USDT" → "btcusdt"
        self.raw_symbols = [self._to_stream_symbol(s) for s in symbols]
        self._running    = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @staticmethod
    def _to_stream_symbol(ccxt_symbol: str) -> str:
        """'BTC/USDT:USDT'  →  'btcusdt'"""
        return ccxt_symbol.split("/")[0].lower() + "usdt"

    def _build_ws_url(self, symbols: list[str]) -> str:
        """Build combined-stream URL for up to MAX_SYMBOLS_PER_CONNECTION."""
        streams = "/".join(f"{s}@miniTicker" for s in symbols)
        base    = config.WS_URL
        return f"{base}?streams={streams}"

    async def _listen(self, symbols: list[str]):
        url          = self._build_ws_url(symbols)
        backoff      = 1
        max_backoff  = 60

        while self._running:
            try:
                log.info(f"WS connecting: {len(symbols)} symbols")
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    log.success("WS connected ✓")
                    backoff = 1   # reset on successful connect
                    while self._running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            log.warning("WS timeout — reconnecting")
                            break
                        self._handle_message(msg)

            except websockets.exceptions.ConnectionClosed as e:
                log.warning(f"WS closed: {e}")
            except Exception as e:
                log.error(f"WS error: {e}")

            if not self._running:
                break
            log.info(f"WS reconnecting in {backoff}s…")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    def _handle_message(self, raw: str):
        try:
            msg = json.loads(raw)
            data = msg.get("data", msg)   # combined stream wraps in "data"
            if data.get("e") == "24hrMiniTicker":
                symbol = data["s"]
                price_store.update(symbol, {
                    "price":      float(data["c"]),
                    "open":       float(data["o"]),
                    "high":       float(data["h"]),
                    "low":        float(data["l"]),
                    "volume":     float(data["v"]),
                    "quote_vol":  float(data["q"]),
                    "ts":         time.time(),
                })
        except Exception as e:
            log.debug(f"WS parse error: {e}")

    async def _run(self):
        # Split into chunks if > MAX_SYMBOLS_PER_CONNECTION
        chunks  = [
            self.raw_symbols[i:i + self.MAX_SYMBOLS_PER_CONNECTION]
            for i in range(0, len(self.raw_symbols), self.MAX_SYMBOLS_PER_CONNECTION)
        ]
        tasks = [self._listen(chunk) for chunk in chunks]
        await asyncio.gather(*tasks)

    def start(self):
        """Start the WebSocket listener in a daemon thread."""
        if self._running:
            return
        self._running = True
        self._loop    = asyncio.new_event_loop()

        def _worker():
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._run())

        self._thread = threading.Thread(target=_worker, daemon=True, name="ws-listener")
        self._thread.start()
        log.info("WebSocket listener thread started")

    def stop(self):
        """Gracefully shut down the WebSocket listener."""
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        log.info("WebSocket listener stopped")

    def get_price(self, ccxt_symbol: str) -> Optional[float]:
        raw = self._to_stream_symbol(ccxt_symbol)
        d   = price_store.get(raw.upper())
        return d["price"] if d else None

    def get_all(self) -> dict:
        return price_store.all()
