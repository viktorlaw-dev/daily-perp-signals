"""
Run the signal scanner across all top-N symbols.

Usage:
    python -m src.run_signals

This fetches the latest market data for all top symbols and prints any
valid signals it finds. If no signals trigger, it tells you that too.

When Telegram credentials are configured in .env, signals are also
sent as Telegram alerts.
"""

import logging

from src.signal_engine import scan_all_symbols, format_signal
from src.bybit_client import get_top_symbols
from src.telegram_notifier import send_signal_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    print("=" * 60)
    print("  DAILY PERP SIGNALS — Signal Scanner")
    print("=" * 60)

    symbols = get_top_symbols()
    print(f"\nScanning {len(symbols)} symbols by 24h volume...")
    for i, s in enumerate(symbols, 1):
        vol = float(s.get("volume24h", 0))
        print(f"  {i:>2}. {s['symbol']:<15} vol={vol:>15,.0f}")

    print(f"\nRunning signal engine on each symbol...\n")

    signals = scan_all_symbols()

    if not signals:
        print("No signals found this scan. Market conditions may not be right.")
        print("The bot will check again on the next 15-minute cycle.")
    else:
        print(f"Found {len(signals)} signal(s):\n")
        for sig in signals:
            print(format_signal(sig))
            print("\n" + "-" * 50 + "\n")

            sent = send_signal_alert(sig)
            if sent:
                logger.info(f"Telegram alert sent for {sig['symbol']}")
            else:
                logger.warning(f"Failed to send Telegram alert for {sig['symbol']}")

    print("Scan complete.")


if __name__ == "__main__":
    main()
