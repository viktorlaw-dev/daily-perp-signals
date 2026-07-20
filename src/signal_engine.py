"""
Signal Engine — the core logic that turns raw market data into trade signals.

How it works (step by step):

1. PULL DATA
   - Fetch 200 x 1H candles → compute EMA 50 and EMA 200 → decide trend direction.
   - Fetch 200 x 15M candles → compute RSI and MACD → look for pullback entries.
   - Fetch funding rate and open interest → adjust confidence.

2. TREND FILTER (1H)
   - Price above BOTH EMAs + fast EMA > slow EMA → LONG bias.
   - Price below BOTH EMAs + fast EMA < slow EMA → SHORT bias.
   - EMAs tangled (crossing each other) → NO TRADE (skip, choppy market).

3. MOMENTUM CONFIRMATION (15M)
   - For a LONG: wait for RSI to dip toward 40-50 (pullback), then turn up.
     MACD bullish cross (MACD line crosses above signal line) adds confidence.
   - For a SHORT: wait for RSI to rise toward 50-60, then turn down.
     MACD bearish cross adds confidence.

4. FUNDING / OI CONFIDENCE
   - If funding is strongly positive and we're going LONG → crowded long risk → lower confidence.
   - If funding is strongly negative and we're going SHORT → crowded short risk → lower confidence.
   - This doesn't block trades — it just adjusts the confidence tag.

5. OUTPUT
   - Returns a signal dict with direction, entry zone, stop-loss, take-profit, etc.
   - Returns None if no valid signal is found.
"""

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from src import bybit_client
from src.config import (
    EMA_FAST_PERIOD,
    EMA_SLOW_PERIOD,
    FUNDING_CROWDING_THRESHOLD,
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    MAX_RISK_PER_TRADE_PCT,
    RSI_PERIOD,
    SWING_LOOKBACK,
    TARGET_RR,
    TIMEFRAME_15M,
    TIMEFRAME_1H,
)


# ---------------------------------------------------------------------------
# Indicator calculations
# ---------------------------------------------------------------------------

def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """
    Compute an Exponential Moving Average (EMA).

    An EMA is a running average that gives MORE weight to recent prices
    and LESS weight to older prices.  A 50-period EMA on the 1H chart
    gives you the average price over roughly the last 2 days, weighted
    toward the most recent hours.

    Args:
        series: A pandas Series of prices (e.g., close prices).
        period: How many candles to average (e.g., 50 or 200).

    Returns:
        A new Series the same length as the input, with EMA values.
        The first (period - 1) values will be NaN (not enough data yet).
    """
    return series.ewm(span=period, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Compute the Relative Strength Index (RSI).

    RSI measures how fast prices are rising vs falling, on a scale of 0-100.
    - Above 70 = potentially overbought (price may drop).
    - Below 30 = potentially oversold (price may bounce).
    - Around 40-50 = neutral-to-weak, good area for pullback entries in an uptrend.

    We use a 14-period RSI on 15-minute candles.

    How it works:
      1. Calculate price changes from one candle to the next.
      2. Separate gains (positive changes) from losses (negative changes).
      3. Average the gains and average the losses over the period.
      4. RSI = 100 - (100 / (1 + average_gain / average_loss))

    Args:
        series: A pandas Series of close prices.
        period: Lookback period (default 14).

    Returns:
        A Series of RSI values between 0 and 100.
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def compute_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    """
    Compute MACD (Moving Average Convergence Divergence).

    MACD has three parts:
      1. MACD Line = fast EMA (12) minus slow EMA (26).
         When the fast EMA is above the slow EMA, the MACD line is positive (bullish).
      2. Signal Line = EMA(9) of the MACD line itself.
         This smooths the MACD line to reduce noise.
      3. Histogram = MACD line minus signal line.
         Positive histogram = bullish momentum. Negative = bearish.

    A "bullish cross" happens when the MACD line crosses ABOVE the signal line.
    A "bearish cross" happens when the MACD line crosses BELOW the signal line.

    Args:
        series: A pandas Series of close prices.
        fast: Fast EMA period (default 12).
        slow: Slow EMA period (default 26).
        signal_period: Signal line EMA period (default 9).

    Returns:
        A DataFrame with columns: macd, signal, histogram.
    """
    ema_fast = compute_ema(series, fast)
    ema_slow = compute_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal_period)
    histogram = macd_line - signal_line

    return pd.DataFrame({
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
    })


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def klines_to_dataframe(klines: list[list]) -> pd.DataFrame:
    """
    Convert raw Bybit kline data into a pandas DataFrame.

    Bybit kline format (oldest first):
    [startTime, open, high, low, close, volume, turnover]

    A DataFrame is like a spreadsheet — each row is one candle, each column
    is a field (open, high, low, close, volume, etc.).

    Args:
        klines: Raw kline list from the Bybit API.

    Returns:
        A DataFrame with columns: time, open, high, low, close, volume, turnover.
        Prices are floats so we can do math on them.
    """
    df = pd.DataFrame(klines, columns=[
        "time", "open", "high", "low", "close", "volume", "turnover",
    ])
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["time"] = pd.to_datetime(df["time"].astype(int), unit="ms", utc=True)
    return df


# ---------------------------------------------------------------------------
# Signal logic
# ---------------------------------------------------------------------------

def classify_trend(df_1h: pd.DataFrame) -> str:
    """
    Classify the dominant trend using EMA 50 and EMA 200 on the 1H chart.

    Rules:
    - LONG:  close > EMA50 AND close > EMA200 AND EMA50 > EMA200
    - SHORT: close < EMA50 AND close < EMA200 AND EMA50 < EMA200
    - NO_TRADE: anything else (EMAs tangled, price between them, etc.)

    Args:
        df_1h: DataFrame of 1H candles with a 'close' column.

    Returns:
        "LONG", "SHORT", or "NO_TRADE".
    """
    close = df_1h["close"]
    ema_fast = compute_ema(close, EMA_FAST_PERIOD)
    ema_slow = compute_ema(close, EMA_SLOW_PERIOD)

    latest_close = close.iloc[-1]
    latest_ema_fast = ema_fast.iloc[-1]
    latest_ema_slow = ema_slow.iloc[-1]

    if (
        latest_close > latest_ema_fast
        and latest_close > latest_ema_slow
        and latest_ema_fast > latest_ema_slow
    ):
        return "LONG"

    if (
        latest_close < latest_ema_fast
        and latest_close < latest_ema_slow
        and latest_ema_fast < latest_ema_slow
    ):
        return "SHORT"

    return "NO_TRADE"


def detect_pullback(df_15m: pd.DataFrame, direction: str) -> dict[str, Any] | None:
    """
    Detect a pullback entry on the 15M chart.

    For a LONG pullback:
      - RSI dipped toward 40-50 range and is now turning UP.
      - "Turning up" means RSI was falling and is now rising.
      - MACD bullish cross (MACD line crossing above signal line) is a bonus.

    For a SHORT pullback:
      - RSI rose toward 50-60 range and is now turning DOWN.
      - MACD bearish cross is a bonus.

    Args:
        df_15m: DataFrame of 15M candles with a 'close' column.
        direction: "LONG" or "SHORT".

    Returns:
        A dict with pullback details (rsi_value, macd_cross, confirmed), or None
        if no pullback is detected.
    """
    close = df_15m["close"]

    rsi = compute_rsi(close, RSI_PERIOD)
    macd_df = compute_macd(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL)

    # Look at the last 3 candles for RSI turning point.
    # We need at least 3 candles of RSI data.
    if len(rsi.dropna()) < 3:
        return None

    rsi_prev2 = rsi.iloc[-3]
    rsi_prev1 = rsi.iloc[-2]
    rsi_now = rsi.iloc[-1]

    # Check MACD cross on the latest candle.
    macd_now = macd_df["macd"].iloc[-1]
    signal_now = macd_df["signal"].iloc[-1]
    macd_prev = macd_df["macd"].iloc[-2]
    signal_prev = macd_df["signal"].iloc[-2]

    if direction == "LONG":
        # RSI pulled back into the 38-52 zone and is now turning up.
        rsi_in_zone = 38 <= rsi_prev1 <= 55
        rsi_turning_up = rsi_now > rsi_prev1
        bullish_cross = (macd_prev <= signal_prev) and (macd_now > signal_now)

        if rsi_in_zone and rsi_turning_up:
            return {
                "confirmed": True,
                "rsi_value": round(rsi_now, 2),
                "rsi_pullback": round(rsi_prev1, 2),
                "rsi_zone": "pulled back to 40-50, now rising",
                "macd_cross": "bullish" if bullish_cross else "none",
            }

    elif direction == "SHORT":
        # RSI rallied into the 48-62 zone and is now turning down.
        rsi_in_zone = 48 <= rsi_prev1 <= 65
        rsi_turning_down = rsi_now < rsi_prev1
        bearish_cross = (macd_prev >= signal_prev) and (macd_now < signal_now)

        if rsi_in_zone and rsi_turning_down:
            return {
                "confirmed": True,
                "rsi_value": round(rsi_now, 2),
                "rsi_pullback": round(rsi_prev1, 2),
                "rsi_zone": "rallied to 50-60, now falling",
                "macd_cross": "bearish" if bearish_cross else "none",
            }

    return None


def find_swing_point(df_15m: pd.DataFrame, direction: str) -> float:
    """
    Find the recent swing point to use as a stop-loss level.

    For a LONG trade: the stop goes below the recent swing LOW.
    We look at the lowest low in the last N candles.

    For a SHORT trade: the stop goes above the recent swing HIGH.
    We look at the highest high in the last N candles.

    Args:
        df_15m: DataFrame of 15M candles with 'high' and 'low' columns.
        direction: "LONG" or "SHORT".

    Returns:
        The swing point price (float).
    """
    lookback = df_15m.tail(SWING_LOOKBACK)

    if direction == "LONG":
        return float(lookback["low"].min())
    else:
        return float(lookback["high"].max())


def assess_confidence(funding_rate: float, direction: str) -> dict[str, Any]:
    """
    Assess signal confidence based on funding rate and open interest context.

    Funding rate basics:
    - When funding is POSITIVE, longs pay shorts. This means the market is
      crowded with longs — there's a cost to holding long positions.
    - When funding is NEGATIVE, shorts pay longs. Market is crowded with shorts.
    - Near zero = balanced.

    Rules:
    - Strongly positive funding + LONG signal → "crowded long" → lower confidence.
    - Strongly negative funding + SHORT signal → "crowded short" → lower confidence.
    - Otherwise → full confidence.

    Args:
        funding_rate: Current funding rate (e.g., 0.0001 = 0.01%).
        direction: "LONG" or "SHORT".

    Returns:
        A dict with confidence level and reasoning string.
    """
    abs_funding = abs(funding_rate)

    if abs_funding < FUNDING_CROWDING_THRESHOLD * 0.5:
        label = "neutral"
        reasoning = "Funding neutral — no crowding risk."
        confidence_boost = 1  # full confidence
    elif (
        (direction == "LONG" and funding_rate > FUNDING_CROWDING_THRESHOLD)
        or (direction == "SHORT" and funding_rate < -FUNDING_CROWDING_THRESHOLD)
    ):
        label = "crowded"
        reasoning = (
            f"Funding {funding_rate:+.6f} — "
            f"crowded {'long' if direction == 'LONG' else 'short'}, "
            f"reduced confidence."
        )
        confidence_boost = 0  # downgrade confidence
    else:
        label = "aligned"
        side = "long" if direction == "LONG" else "short"
        reasoning = f"Funding {funding_rate:+.6f} — favors the {side} side."
        confidence_boost = 1

    return {
        "level": label,
        "reasoning": reasoning,
        "confidence_boost": confidence_boost,
    }


# ---------------------------------------------------------------------------
# Main signal generator
# ---------------------------------------------------------------------------

def generate_signal(symbol: str) -> dict[str, Any] | None:
    """
    Attempt to generate a trade signal for a single symbol.

    This is the main function that ties everything together:
    1. Fetch 1H candles → classify trend.
    2. If trend is NO_TRADE → return None immediately.
    3. Fetch 15M candles → detect pullback + MACD confirmation.
    4. If no pullback → return None.
    5. Calculate entry, stop-loss, take-profit.
    6. Fetch funding rate → assess confidence.
    7. Return a complete signal dict.

    Args:
        symbol: Trading pair, e.g., "BTCUSDT".

    Returns:
        A signal dict if conditions are met, or None if no signal.
    """
    # --- Step 1: Trend filter (1H) ---
    klines_1h = bybit_client.get_klines(symbol, TIMEFRAME_1H, limit=200)
    df_1h = klines_to_dataframe(klines_1h)
    direction = classify_trend(df_1h)

    if direction == "NO_TRADE":
        return None

    # --- Step 2: Pullback confirmation (15M) ---
    klines_15m = bybit_client.get_klines(symbol, TIMEFRAME_15M, limit=200)
    df_15m = klines_to_dataframe(klines_15m)
    pullback = detect_pullback(df_15m, direction)

    if pullback is None:
        return None

    # --- Step 3: Calculate entry, SL, TP ---
    current_close = float(df_15m["close"].iloc[-1])
    swing_point = find_swing_point(df_15m, direction)

    if direction == "LONG":
        entry_zone_low = current_close
        entry_zone_high = round(current_close * 1.003, 6)  # 0.3% zone above
        stop_loss = round(swing_point * 0.998, 6)  # 0.2% below swing low
        risk_per_unit = current_close - stop_loss
        take_profit = round(current_close + (risk_per_unit * TARGET_RR), 6)
    else:  # SHORT
        entry_zone_low = round(current_close * 0.997, 6)  # 0.3% zone below
        entry_zone_high = current_close
        stop_loss = round(swing_point * 1.002, 6)  # 0.2% above swing high
        risk_per_unit = stop_loss - current_close
        take_profit = round(current_close - (risk_per_unit * TARGET_RR), 6)

    rr_ratio = TARGET_RR

    # --- Step 4: Funding / OI confidence ---
    funding_info = bybit_client.get_funding_rate(symbol)
    funding = assess_confidence(funding_info["fundingRate"], direction)
    oi = funding_info["openInterest"]

    # Determine final confidence tag.
    if funding["confidence_boost"] == 1 and pullback["macd_cross"] != "none":
        confidence = "HIGH"
    elif funding["confidence_boost"] == 1:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # --- Step 5: Build the signal dict ---
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    signal = {
        "symbol": symbol,
        "direction": direction,
        "confidence": confidence,
        "entry_zone": {
            "low": entry_zone_low,
            "high": entry_zone_high,
        },
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "risk_reward": rr_ratio,
        "suggested_risk_pct": MAX_RISK_PER_TRADE_PCT,
        "indicators": {
            "trend_1h": f"EMA{EMA_FAST_PERIOD}/{EMA_SLOW_PERIOD} aligned",
            "rsi_15m": pullback["rsi_value"],
            "rsi_pullback": pullback["rsi_pullback"],
            "rsi_context": pullback["rsi_zone"],
            "macd_15m": pullback["macd_cross"],
            "funding_rate": funding_info["fundingRate"],
            "funding_context": funding["reasoning"],
            "open_interest": oi,
        },
        "generated_at": now_utc,
    }

    return signal


def format_signal(signal: dict[str, Any]) -> str:
    """
    Format a signal dict into the Option C Telegram message.

    This produces the detailed multi-line alert that looks like:

    📊 New Signal Generated
    Symbol: SOLUSDT
    Direction: LONG
    Confidence: HIGH
    ...

    Args:
        signal: A signal dict from generate_signal().

    Returns:
        A formatted string ready to send to Telegram.
    """
    ind = signal["indicators"]
    entry = signal["entry_zone"]

    # Direction arrow (ASCII-safe for terminal).
    trend_label = "LONG" if signal["direction"] == "LONG" else "SHORT"

    # MACD line for context.
    macd_text = ind["macd_15m"].capitalize() if ind["macd_15m"] != "none" else "No cross"

    # Funding status.
    fr = ind["funding_rate"]
    if abs(fr) < FUNDING_CROWDING_THRESHOLD * 0.5:
        fund_status = "[OK]"
    elif "crowded" in ind["funding_context"].lower():
        fund_status = "[WARN]"
    else:
        fund_status = "[OK]"

    lines = [
        "=== NEW SIGNAL ===",
        "",
        f"Symbol:     {signal['symbol']}",
        f"Direction:  {trend_label}",
        f"Confidence: {signal['confidence']}",
        "",
        f"Entry Zone: {entry['low']} - {entry['high']}",
        f"Stop Loss:  {signal['stop_loss']}",
        f"Take Profit:{signal['take_profit']}",
        "",
        f"Risk per Trade: {signal['suggested_risk_pct']}%",
        f"Estimated R:R:  1:{signal['risk_reward']:.1f}",
        "",
        f"Trend (1H):  {ind['trend_1h']}",
        f"Momentum:    RSI {ind['rsi_pullback']} -> {ind['rsi_15m']} ({ind['rsi_context']}), MACD {macd_text}",
        f"Funding/OI:  {ind['funding_context']} {fund_status}",
        f"Open Interest: {ind['open_interest']:,.0f}",
        "",
        f"Generated: {signal['generated_at']}",
    ]

    return "\n".join(lines)


def scan_all_symbols() -> list[dict[str, Any]]:
    """
    Scan all top-N symbols and return any valid signals found.

    Returns:
        A list of signal dicts (may be empty if no symbols trigger a signal).
    """
    symbols = bybit_client.get_top_symbols()
    signals = []

    for s in symbols:
        sym = s["symbol"]
        try:
            result = generate_signal(sym)
            if result is not None:
                signals.append(result)
        except Exception as e:
            # Don't let one symbol's error kill the whole scan.
            print(f"[SKIP] {sym}: {e}")
            continue

    return signals
