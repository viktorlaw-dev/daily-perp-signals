"""
Quick sanity check: pull the top 15 symbols by volume, then fetch
klines, funding rate, and OI for the top symbol.
"""

from src.bybit_client import get_top_symbols, get_klines, get_funding_rate


def main() -> None:
    print("Fetching top 15 symbols by 24h volume (filtered)...\n")
    symbols = get_top_symbols()

    print(f"{'#':>3}  {'Symbol':>15}  {'24h Volume':>15}  {'Last Price':>12}  {'Funding':>10}  {'OI':>12}")
    print("-" * 80)
    for i, s in enumerate(symbols, 1):
        vol = float(s.get("volume24h", 0))
        price = float(s.get("lastPrice", 0))
        fr = float(s.get("fundingRate", 0)) if s.get("fundingRate") else 0.0
        oi = float(s.get("openInterest", 0)) if s.get("openInterest") else 0.0
        print(f"{i:>3}  {s['symbol']:>15}  {vol:>15,.0f}  {price:>12,.2f}  {fr:>10.6f}  {oi:>12,.0f}")

    if not symbols:
        print("No symbols found. Check API connection.")
        return

    top = symbols[0]
    sym = top["symbol"]
    print(f"\n--- Detailed check for {sym} ---\n")

    print("Fetching 5 recent 1H candles...")
    klines = get_klines(sym, "60", limit=5)
    for k in klines:
        print(f"  {k}")

    print(f"\nFetching 5 recent 15M candles...")
    klines_15m = get_klines(sym, "15", limit=5)
    for k in klines_15m:
        print(f"  {k}")

    print(f"\nFetching funding rate & OI...")
    fund = get_funding_rate(sym)
    print(f"  Funding Rate: {fund['fundingRate']:.6f}")
    print(f"  Open Interest: {fund['openInterest']:,.0f}")

    print("\nAll checks passed!")


if __name__ == "__main__":
    main()
