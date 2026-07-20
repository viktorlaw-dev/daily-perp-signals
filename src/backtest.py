"""
Backtester — walk-forward simulation of the signal strategy.

This replays historical data candle-by-candle and asks:
  "If I had followed the signal rules, would I have made money?"

Key rules:
  - NO peeking into the future. At each candle, we only see data up to that point.
  - One trade at a time per symbol. No position stacking.
  - Entry at the close of the signal candle.
  - SL/TP checked against the high/low of subsequent candles.
  - If both SL and TP could be hit in the same candle, SL wins (conservative).
  - No time limit — trades stay open until SL or TP is hit.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from src import bybit_client
from src.config import (
    BACKTEST_BLACKLIST,
    COOLDOWN_CANDLES,
    EMA_FAST_PERIOD,
    EMA_SLOW_PERIOD,
    MACD_CROSS_REQUIRED,
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    RSI_PERIOD,
    SWING_LOOKBACK,
    TARGET_RR,
    TIMEFRAME_15M,
    TIMEFRAME_1H,
)
from src.signal_engine import (
    classify_trend,
    compute_ema,
    compute_macd,
    compute_rsi,
    detect_pullback,
    find_swing_point,
    klines_to_dataframe,
)


# ---------------------------------------------------------------------------
# Historical data fetching
# ---------------------------------------------------------------------------

def fetch_historical_klines(
    symbol: str,
    interval: str,
    months: int = 3,
) -> pd.DataFrame:
    """
    Pull historical klines from Bybit by paginating through the API.

    Bybit returns at most 1000 candles per request, newest FIRST.
    We paginate backward: start from now, get the newest 1000 candles,
    then set end = oldest_candle_time - 1ms to get the next older batch.

    Args:
        symbol: Trading pair, e.g., "BTCUSDT".
        interval: Bybit interval string ("60" for 1H, "15" for 15M).
        months: How many months of history to fetch.

    Returns:
        A DataFrame with all candles (oldest first).
    """
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=months * 30)).timestamp() * 1000
    )

    all_klines: list[list] = []
    cursor = end_ms  # Start from now, work backward.
    interval_ms = int(interval) * 60_000

    while cursor > start_ms:
        payload = bybit_client._request(
            "/v5/market/kline",
            params={
                "category": "linear",
                "symbol": symbol,
                "interval": interval,
                "start": start_ms,
                "end": cursor,
                "limit": 1000,
            },
        )
        batch = payload["result"]["list"]
        if not batch:
            break

        all_klines.extend(batch)

        # Move cursor BEFORE the oldest candle in this batch.
        oldest_ts = int(batch[-1][0])
        cursor = oldest_ts - 1  # 1ms before oldest candle.

        # Politeness pause.
        time.sleep(0.05)

    if not all_klines:
        return pd.DataFrame()

    # Deduplicate by timestamp.
    seen = set()
    unique = []
    for k in all_klines:
        ts = k[0]
        if ts not in seen:
            seen.add(ts)
            unique.append(k)

    unique.sort(key=lambda x: int(x[0]))
    return klines_to_dataframe(unique)


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------

def simulate_trade(
    df_15m: pd.DataFrame,
    signal_index: int,
    direction: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    target_rr: float = TARGET_RR,
) -> dict[str, Any]:
    """
    Walk forward from the signal candle and check if SL or TP gets hit.

    For each subsequent candle:
      - If direction is LONG:
          SL hit = candle low <= stop_loss
          TP hit = candle high >= take_profit
      - If direction is SHORT:
          SL hit = candle high >= stop_loss
          TP hit = candle low <= take_profit

    If both could be hit in the same candle, we assume SL hit first (conservative).

    Args:
        df_15m: Full 15M DataFrame for the symbol.
        signal_index: Index of the signal candle in df_15m.
        direction: "LONG" or "SHORT".
        entry: Entry price.
        stop_loss: Stop-loss price.
        take_profit: Take-profit price.

    Returns:
        A dict with trade result: r_multiple, exit_price, candles_held, result ("WIN" or "LOSS").
    """
    # Walk through candles AFTER the signal candle.
    for i in range(signal_index + 1, len(df_15m)):
        candle = df_15m.iloc[i]
        high = candle["high"]
        low = candle["low"]

        if direction == "LONG":
            sl_hit = low <= stop_loss
            tp_hit = high >= take_profit
        else:  # SHORT
            sl_hit = high >= stop_loss
            tp_hit = low <= take_profit

        if sl_hit and tp_hit:
            # Both could fire — assume SL hits first (conservative).
            return {
                "result": "LOSS",
                "r_multiple": -1.0,
                "exit_price": stop_loss,
                "candles_held": i - signal_index,
            }
        elif sl_hit:
            return {
                "result": "LOSS",
                "r_multiple": -1.0,
                "exit_price": stop_loss,
                "candles_held": i - signal_index,
            }
        elif tp_hit:
            return {
                "result": "WIN",
                "r_multiple": target_rr,
                "exit_price": take_profit,
                "candles_held": i - signal_index,
            }

    # Trade never resolved — hit end of data without SL or TP.
    # Count it as an open/inconclusive trade.
    last_close = float(df_15m["close"].iloc[-1])
    if direction == "LONG":
        unrealized = (last_close - entry) / (entry - stop_loss) if entry != stop_loss else 0
    else:
        unrealized = (entry - last_close) / (stop_loss - entry) if stop_loss != entry else 0

    return {
        "result": "OPEN",
        "r_multiple": round(unrealized, 2),
        "exit_price": last_close,
        "candles_held": len(df_15m) - 1 - signal_index,
    }


# ---------------------------------------------------------------------------
# Walk-forward backtest for one symbol
# ---------------------------------------------------------------------------

def backtest_symbol(
    symbol: str,
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
    target_rr: float | None = None,
    swing_lookback: int | None = None,
    cooldown_candles: int | None = None,
    macd_cross_required: bool | None = None,
) -> list[dict[str, Any]]:
    """
    Walk-forward backtest for a single symbol.

    Supports config overrides for parameter testing (target_rr, swing_lookback, etc.).
    If an override is None, falls back to the global config value.

    Args:
        symbol: Trading pair (for labeling).
        df_1h: Full historical 1H DataFrame.
        df_15m: Full historical 15M DataFrame.
        target_rr: Override for TARGET_RR.
        swing_lookback: Override for SWING_LOOKBACK.
        cooldown_candles: Override for COOLDOWN_CANDLES.
        macd_cross_required: Override for MACD_CROSS_REQUIRED.

    Returns:
        A list of trade result dicts.
    """
    if df_1h.empty or df_15m.empty:
        return []

    # Resolve parameter overrides.
    rr = target_rr if target_rr is not None else TARGET_RR
    sl_lookback = swing_lookback if swing_lookback is not None else SWING_LOOKBACK
    cooldown = cooldown_candles if cooldown_candles is not None else COOLDOWN_CANDLES
    macd_required = macd_cross_required if macd_cross_required is not None else MACD_CROSS_REQUIRED

    trades: list[dict[str, Any]] = []
    in_trade = False
    trade_end_index = -1  # Index where the last trade ended.
    cooldown_end_index = -1  # Index where cooldown expires.

    min_1h_candles = EMA_SLOW_PERIOD + 10

    for idx in range(sl_lookback + 5, len(df_15m)):
        current_time = df_15m.iloc[idx]["time"]

        # Skip if we're still in a trade.
        if in_trade and idx <= trade_end_index:
            continue
        in_trade = False

        # Skip if we're still in cooldown.
        if cooldown > 0 and idx <= cooldown_end_index:
            continue

        # Get all 1H candles up to current_time.
        mask_1h = df_1h["time"] <= current_time
        df_1h_slice = df_1h.loc[mask_1h]

        if len(df_1h_slice) < min_1h_candles:
            continue

        # --- Step A: Classify trend ---
        direction = classify_trend(df_1h_slice)
        if direction == "NO_TRADE":
            continue

        # --- Step B: Detect pullback on 15M up to current candle ---
        df_15m_slice = df_15m.iloc[: idx + 1]
        pullback = detect_pullback(df_15m_slice, direction)
        if pullback is None:
            continue

        # --- Step B2: MACD cross requirement ---
        if macd_required and pullback["macd_cross"] == "none":
            continue

        # --- Step C: Calculate entry, SL, TP ---
        entry = float(df_15m_slice["close"].iloc[-1])
        swing = find_swing_point(df_15m_slice, direction)

        if direction == "LONG":
            stop_loss = round(swing * 0.998, 6)
            risk = entry - stop_loss
            if risk <= 0:
                continue
            take_profit = round(entry + (risk * rr), 6)
        else:
            stop_loss = round(swing * 1.002, 6)
            risk = stop_loss - entry
            if risk <= 0:
                continue
            take_profit = round(entry - (risk * rr), 6)

        # --- Step D: Simulate trade ---
        result = simulate_trade(df_15m, idx, direction, entry, stop_loss, take_profit, rr)

        trade = {
            "symbol": symbol,
            "direction": direction,
            "entry_time": str(current_time),
            "entry": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "r_multiple": result["r_multiple"],
            "result": result["result"],
            "candles_held": result["candles_held"],
        }
        trades.append(trade)

        # Mark in-trade and cooldown periods.
        if result["result"] != "OPEN":
            trade_end_index = idx + result["candles_held"]
            cooldown_end_index = trade_end_index + cooldown
            in_trade = True

    return trades


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Compute summary statistics from a list of trade results.

    Metrics:
      - Total signals, wins, losses, open trades
      - Win rate
      - Average R-multiple
      - Expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
      - Profit factor = gross_profit / gross_loss
      - Max consecutive losses
      - Total R gained

    Args:
        trades: List of trade dicts with 'r_multiple' and 'result' fields.

    Returns:
        A dict of computed metrics.
    """
    if not trades:
        return {
            "total": 0, "wins": 0, "losses": 0, "open": 0,
            "win_rate": 0, "avg_r": 0, "expectancy": 0,
            "profit_factor": 0, "max_consec_losses": 0, "total_r": 0,
        }

    total = len(trades)
    wins = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    open_trades = [t for t in trades if t["result"] == "OPEN"]

    n_wins = len(wins)
    n_losses = len(losses)
    n_open = len(open_trades)
    n_closed = n_wins + n_losses

    win_rate = (n_wins / n_closed * 100) if n_closed > 0 else 0

    total_r = sum(t["r_multiple"] for t in trades)
    avg_r = total_r / total if total > 0 else 0

    # Expectancy: how much R we expect to make per trade on average.
    avg_win = TARGET_RR  # Always +2R when we win.
    avg_loss = 1.0  # Always -1R when we lose.
    expectancy = (win_rate / 100 * avg_win) - ((100 - win_rate) / 100 * avg_loss)

    # Profit factor: gross profit / gross loss.
    gross_profit = n_wins * TARGET_RR
    gross_loss = n_losses * 1.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    # Max consecutive losses.
    max_consec = 0
    current_streak = 0
    for t in trades:
        if t["result"] == "LOSS":
            current_streak += 1
            max_consec = max(max_consec, current_streak)
        elif t["result"] == "WIN":
            current_streak = 0

    return {
        "total": total,
        "wins": n_wins,
        "losses": n_losses,
        "open": n_open,
        "win_rate": round(win_rate, 1),
        "avg_r": round(avg_r, 3),
        "expectancy": round(expectancy, 3),
        "profit_factor": round(profit_factor, 2),
        "max_consec_losses": max_consec,
        "total_r": round(total_r, 1),
    }


# ---------------------------------------------------------------------------
# Full backtest runner
# ---------------------------------------------------------------------------

def run_full_backtest(
    months: int = 3,
    target_rr: float | None = None,
    swing_lookback: int | None = None,
    cooldown_candles: int | None = None,
    macd_cross_required: bool | None = None,
    blacklist: set[str] | None = None,
) -> dict[str, Any]:
    """
    Run the backtest across all top-N symbols.

    Supports parameter overrides for tuning rounds.
    If blacklist is provided, symbols matching it are skipped entirely.

    Args:
        months: Number of months of history to backtest.
        target_rr: Override for TARGET_RR.
        swing_lookback: Override for SWING_LOOKBACK.
        cooldown_candles: Override for COOLDOWN_CANDLES.
        macd_cross_required: Override for MACD_CROSS_REQUIRED.
        blacklist: Override for BACKTEST_BLACKLIST (set of symbol suffixes to exclude).

    Returns:
        A dict with per-symbol results and overall metrics.
    """
    symbols = bybit_client.get_top_symbols()
    bl = blacklist if blacklist is not None else BACKTEST_BLACKLIST
    all_results: dict[str, Any] = {}

    def _sym_matches_blacklist(sym: str) -> bool:
        base = sym.replace("USDT", "").replace("PERP", "")
        return base.upper() in {b.upper() for b in bl}

    # Filter out blacklisted symbols.
    filtered_symbols = [s for s in symbols if not _sym_matches_blacklist(s["symbol"])]
    skipped_count = len(symbols) - len(filtered_symbols)
    if skipped_count > 0:
        print(f"\nSkipping {skipped_count} blacklisted symbol(s).")

    rr_label = f"R:R={target_rr}" if target_rr else f"R:R={TARGET_RR}"
    sl_label = f"SL={swing_lookback}" if swing_lookback else f"SL={SWING_LOOKBACK}"
    cd_label = f"CD={cooldown_candles}" if cooldown_candles else f"CD={COOLDOWN_CANDLES}"
    macd_label = "MACD=required" if macd_cross_required else "MACD=optional"

    print(f"\nBacktesting {len(filtered_symbols)} symbols over {months} months [{rr_label}, {sl_label}, {cd_label}, {macd_label}]...\n")

    for i, s in enumerate(filtered_symbols, 1):
        sym = s["symbol"]
        print(f"[{i}/{len(filtered_symbols)}] {sym} — fetching data...", end=" ", flush=True)

        try:
            df_1h = fetch_historical_klines(sym, TIMEFRAME_1H, months)
            df_15m = fetch_historical_klines(sym, TIMEFRAME_15M, months)

            if df_1h.empty or df_15m.empty:
                print("SKIPPED (no data)")
                continue

            print(f"1H: {len(df_1h)}c, 15M: {len(df_15m)}c — running...", end=" ", flush=True)

            trades = backtest_symbol(
                sym, df_1h, df_15m,
                target_rr=target_rr,
                swing_lookback=swing_lookback,
                cooldown_candles=cooldown_candles,
                macd_cross_required=macd_cross_required,
            )
            metrics = compute_metrics(trades)

            all_results[sym] = {
                "trades": trades,
                "metrics": metrics,
            }

            print(
                f"{metrics['total']}s, "
                f"{metrics['win_rate']}%wr, "
                f"{metrics['total_r']:+.1f}R, "
                f"pf={metrics['profit_factor']}"
            )

        except Exception as e:
            print(f"ERROR: {e}")
            continue

    # --- Aggregate overall metrics ---
    all_trades = []
    for r in all_results.values():
        all_trades.extend(r["trades"])

    overall = compute_metrics(all_trades)

    return {
        "per_symbol": all_results,
        "overall": overall,
        "all_trades": all_trades,
    }


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def print_report(results: dict[str, Any]) -> None:
    """
    Print a clean summary of the backtest results.

    Shows:
      - Per-symbol breakdown table.
      - Overall metrics.
      - Trade log (last 20 trades).

    Args:
        results: Dict from run_full_backtest().
    """
    per_symbol = results["per_symbol"]
    overall = results["overall"]

    print("\n")
    print("=" * 90)
    print("  BACKTEST RESULTS")
    print("=" * 90)

    # --- Per-symbol table ---
    header = f"{'Symbol':>15}  {'Signals':>7}  {'Wins':>5}  {'Losses':>5}  {'Win%':>6}  {'Avg R':>6}  {'Total R':>8}  {'PF':>5}"
    print(f"\n{header}")
    print("-" * 90)

    for sym, data in per_symbol.items():
        m = data["metrics"]
        print(
            f"{sym:>15}  {m['total']:>7}  {m['wins']:>5}  {m['losses']:>5}  "
            f"{m['win_rate']:>5.1f}%  {m['avg_r']:>+6.3f}  {m['total_r']:>+8.1f}  "
            f"{m['profit_factor']:>5.2f}"
        )

    # --- Overall ---
    print("-" * 90)
    print(
        f"{'OVERALL':>15}  {overall['total']:>7}  {overall['wins']:>5}  "
        f"{overall['losses']:>5}  {overall['win_rate']:>5.1f}%  "
        f"{overall['avg_r']:>+6.3f}  {overall['total_r']:>+8.1f}  "
        f"{overall['profit_factor']:>5.2f}"
    )

    print(f"\n{'Key Metrics:'}")
    print(f"  Total Signals:       {overall['total']}")
    print(f"  Win Rate:            {overall['win_rate']}%")
    print(f"  Average R/Trade:     {overall['avg_r']:+.3f}R")
    print(f"  Expectancy:          {overall['expectancy']:+.3f}R per trade")
    print(f"  Profit Factor:       {overall['profit_factor']:.2f}")
    print(f"  Max Consec. Losses:  {overall['max_consec_losses']}")
    print(f"  Total R Gained:      {overall['total_r']:+.1f}R")
    print(f"  Open/Unresolved:     {overall['open']}")

    # --- Last 20 trades log ---
    all_trades = results["all_trades"]
    if all_trades:
        print(f"\n{'Last 20 Trades:'}")
        print(f"{'#':>4}  {'Symbol':>15}  {'Dir':>5}  {'Entry':>12}  {'Result':>6}  {'R':>6}  {'Held':>5}")
        print("-" * 70)
        for t in all_trades[-20:]:
            print(
                f"{'':>4}  {t['symbol']:>15}  {t['direction']:>5}  "
                f"{t['entry']:>12.4f}  {t['result']:>6}  "
                f"{t['r_multiple']:>+6.2f}  {t['candles_held']:>5}"
            )

    print("\n" + "=" * 90)
