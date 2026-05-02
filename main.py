"""
main.py
─────────────────────────────────────────────────────────────────────────────
CLI entry point — runs the scanner headlessly (no UI).
Useful for:
  • Scheduled cron jobs
  • Serverless / background workers
  • Quick terminal check without launching Streamlit

Usage:
    python main.py                  # single scan, print results
    python main.py --loop           # scan every SCAN_INTERVAL_SEC seconds
    python main.py --symbol BTCUSDT # analyse one symbol only
    python main.py --backtest BTC/USDT:USDT  # quick backtest
─────────────────────────────────────────────────────────────────────────────
"""
import argparse
import time
import os
import sys

import config
from data_fetcher.binance_rest import get_top_symbols, fetch_ohlcv
from scanner.signal_engine import run_scan, analyse_symbol, SignalResult
from alerts.telegram import send_scan_summary, send_signal_alert, send_raw
from utils.logger import log


def _print_results(results: list[SignalResult], top_n: int = 20):
    if not results:
        log.warning("No signals found above threshold.")
        return

    print("\n" + "═" * 80)
    print(f"  {'CRYPTO FUTURES MOMENTUM SCANNER':^76}  ")
    print(f"  {'Top ' + str(min(top_n, len(results))) + ' Signals':^76}  ")
    print("═" * 80)
    fmt = "{:<6} {:<22} {:>8} {:>8} {:>12} {:>10} {:>8}"
    print(fmt.format("Rank", "Symbol", "Price", "Score", "Confidence", "Upside", "24hVol"))
    print("─" * 80)

    for i, r in enumerate(results[:top_n], 1):
        sym  = r.symbol.split("/")[0]
        price = f"${r.price:,.2f}" if r.price >= 1 else f"${r.price:.5f}"
        vol  = f"${r.volume_24h/1e6:.0f}M"
        print(fmt.format(
            f"#{i}", sym, price, f"{r.score:.1f}",
            r.confidence.replace("🔥","").replace("✅","").replace("🟡","").replace("🔵","").strip(),
            f"+{r.upside_est:.1f}%", vol
        ))
        if r.reasons:
            for reason in r.reasons[:2]:
                print(f"        └─ {reason}")

    print("═" * 80 + "\n")


def single_scan():
    log.info("Fetching top symbols…")
    symbols = get_top_symbols()
    if not symbols:
        log.error("No symbols fetched — check connection / API keys")
        sys.exit(1)

    log.info(f"Scanning {len(symbols)} symbols…")
    results = run_scan(symbols)
    _print_results(results)

    # Telegram
    for r in results:
        send_signal_alert(r)
    send_scan_summary(results)

    return results


def loop_scan():
    log.info(f"Starting continuous scan loop (interval={config.SCAN_INTERVAL_SEC}s)")
    send_raw("🚀 Scanner started in loop mode")
    symbols = get_top_symbols()

    while True:
        try:
            log.info("─" * 40)
            results = run_scan(symbols)
            _print_results(results, top_n=10)

            for r in results:
                send_signal_alert(r)
            send_scan_summary(results)

            log.info(f"Sleeping {config.SCAN_INTERVAL_SEC}s…")
            time.sleep(config.SCAN_INTERVAL_SEC)

            # Refresh symbols every 6 cycles (~30 min)
            if int(time.time()) % (config.SCAN_INTERVAL_SEC * 6) < config.SCAN_INTERVAL_SEC:
                log.info("Refreshing symbol list…")
                symbols = get_top_symbols()

        except KeyboardInterrupt:
            log.info("Stopped by user")
            break
        except Exception as e:
            log.error(f"Scan loop error: {e}")
            time.sleep(30)


def quick_backtest(symbol: str):
    from backtest.engine import run_backtest
    log.info(f"Running quick backtest: {symbol}")
    r = run_backtest(symbol, timeframe="4h", days=180)
    if r.error:
        log.error(r.error)
    else:
        for k, v in r.summary().items():
            print(f"  {k:<20}: {v}")


def main():
    parser = argparse.ArgumentParser(description="Crypto Futures Momentum Scanner CLI")
    parser.add_argument("--loop",      action="store_true", help="Continuous scan loop")
    parser.add_argument("--symbol",    type=str, default=None, help="Analyse single symbol (e.g. BTC/USDT:USDT)")
    parser.add_argument("--backtest",  type=str, default=None, help="Quick backtest for symbol")
    parser.add_argument("--top",       type=int, default=20,   help="Show top N results")
    args = parser.parse_args()

    print(f"\n  Mode: {'TESTNET' if config.USE_TESTNET else 'MAINNET'}")
    print(f"  Min volume: ${config.MIN_24H_VOLUME_USDT/1e6:.0f}M\n")

    if args.backtest:
        quick_backtest(args.backtest)
    elif args.symbol:
        from data_fetcher.binance_rest import fetch_ticker_snapshot
        ticker = fetch_ticker_snapshot([args.symbol])
        result = analyse_symbol(args.symbol, ticker.get(args.symbol, {}))
        if result:
            _print_results([result])
        else:
            log.warning(f"No signal found for {args.symbol}")
    elif args.loop:
        loop_scan()
    else:
        single_scan()


if __name__ == "__main__":
    main()
