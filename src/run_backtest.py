"""
Run the backtest with optional tuning overrides.

Usage:
    python -m src.run_backtest              # Baseline (no overrides)
    python -m src.run_backtest --round 1    # MACD cross required
    python -m src.run_backtest --round 2    # SWING_LOOKBACK=12
    python -m src.run_backtest --round 3    # Cooldown=16 candles
    python -m src.run_backtest --round 4    # Blacklist losers
    python -m src.run_backtest --round 5a   # R:R=1.5
    python -m src.run_backtest --round 5b   # R:R=2.5
    python -m src.run_backtest --round 6    # Combined best
"""

import argparse
import time
from datetime import datetime, timezone

from src.backtest import run_full_backtest, print_report

ROUND_4_BLACKLIST = {"RESOLV", "SOON", "SAND", "STRK", "PUMPFUN"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily Perp Signals Backtest")
    parser.add_argument("--round", type=str, default="0", help="Tuning round to run")
    args = parser.parse_args()

    round_label = args.round.upper()
    print("=" * 60)
    print("  DAILY PERP SIGNALS — Backtest")
    print(f"  Round: {round_label}")
    print(f"  Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    # Defaults (baseline).
    kwargs = {}

    if args.round == "1":
        print("\n  Override: MACD cross required\n")
        kwargs["macd_cross_required"] = True

    elif args.round == "2":
        print("\n  Override: SWING_LOOKBACK = 12\n")
        kwargs["swing_lookback"] = 12

    elif args.round == "3":
        print("\n  Override: Cooldown = 16 candles (4 hours)\n")
        kwargs["cooldown_candles"] = 16

    elif args.round == "4":
        print(f"\n  Override: Blacklist = {ROUND_4_BLACKLIST}\n")
        kwargs["blacklist"] = ROUND_4_BLACKLIST

    elif args.round == "5a":
        print("\n  Override: R:R = 1.5\n")
        kwargs["target_rr"] = 1.5

    elif args.round == "5b":
        print("\n  Override: R:R = 2.5\n")
        kwargs["target_rr"] = 2.5

    elif args.round == "5a_bl":
        print(f"\n  Override: R:R = 1.5 + Blacklist {ROUND_4_BLACKLIST}\n")
        kwargs["target_rr"] = 1.5
        kwargs["blacklist"] = ROUND_4_BLACKLIST

    elif args.round == "5b_bl":
        print(f"\n  Override: R:R = 2.5 + Blacklist {ROUND_4_BLACKLIST}\n")
        kwargs["target_rr"] = 2.5
        kwargs["blacklist"] = ROUND_4_BLACKLIST

    elif args.round == "6":
        print(f"\n  Combined: MACD required + SL=12 + CD=16 + Blacklist {ROUND_4_BLACKLIST} + R:R=2.0\n")
        kwargs["macd_cross_required"] = True
        kwargs["swing_lookback"] = 12
        kwargs["cooldown_candles"] = 16
        kwargs["blacklist"] = ROUND_4_BLACKLIST

    else:
        print("\n  Baseline (no overrides)\n")

    start = time.time()
    results = run_full_backtest(months=3, **kwargs)
    elapsed = time.time() - start

    print_report(results)
    print(f"\nBacktest completed in {elapsed:.1f} seconds.")


if __name__ == "__main__":
    main()
