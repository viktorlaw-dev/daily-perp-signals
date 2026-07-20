"""
Low-level client for Bybit Testnet market-data endpoints.

Bybit market data is public — no API signature is required for the endpoints
we use here. We still read optional credentials from .env in case they are
needed later.
"""

import re
import time
from typing import Any

import requests

from src.config import (
    BACKTEST_BLACKLIST,
    BYBIT_API_KEY,
    BYBIT_BASE_URL,
    MEMECOIN_DENYLIST,
    TOP_N_SYMBOLS,
)


def _request(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Make a GET request to the Bybit Testnet API.

    Args:
        path: API endpoint path (e.g., "/v5/market/kline").
        params: Query parameters for the request.

    Returns:
        Parsed JSON response as a dictionary.
    """
    url = f"{BYBIT_BASE_URL}{path}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if BYBIT_API_KEY:
        headers["X-BAPI-API-KEY"] = BYBIT_API_KEY

    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    payload = response.json()

    if payload.get("retCode") != 0:
        raise RuntimeError(
            f"Bybit API error: {payload.get('retCode')} - {payload.get('retMsg')}"
        )

    time.sleep(0.05)
    return payload


def _is_dated_contract(symbol: str) -> bool:
    """Check if a symbol is a dated/expiring contract (e.g., DOGEUSDT-28AUG26)."""
    return bool(re.search(r"\d{1,2}[A-Z]{3}\d{2}", symbol))


def _passes_denylist(symbol: str) -> bool:
    """Check if a symbol contains any memecoin keyword."""
    base = symbol.replace("USDT", "").replace("PERP", "")
    for word in MEMECOIN_DENYLIST:
        if word in base.upper():
            return False
    return True


def _passes_blacklist(symbol: str) -> bool:
    """Check if a symbol is in the backtest-proven blacklist."""
    base = symbol.replace("USDT", "").replace("PERP", "")
    return base.upper() not in {b.upper() for b in BACKTEST_BLACKLIST}


def get_all_tickers() -> list[dict[str, Any]]:
    """
    Fetch all USDT-margined perpetual tickers from Bybit.

    Returns:
        List of ticker dictionaries with symbol, price, volume, funding, OI, etc.
    """
    payload = _request(
        "/v5/market/tickers",
        params={"category": "linear"},
    )
    return payload["result"]["list"]


def get_top_symbols() -> list[dict[str, Any]]:
    """
    Get top N symbols by 24h volume, excluding dated contracts and memecoins.

    Returns:
        List of ticker dicts sorted by 24h volume descending, length = TOP_N_SYMBOLS.
        Each dict includes: symbol, lastPrice, volume24h, fundingRate, openInterest.
    """
    tickers = get_all_tickers()

    filtered = []
    for t in tickers:
        sym = t["symbol"]
        if _is_dated_contract(sym):
            continue
        if not _passes_denylist(sym):
            continue
        if not _passes_blacklist(sym):
            continue
        # Skip tickers with missing or zero volume.
        try:
            vol = float(t.get("volume24h", 0))
        except (ValueError, TypeError):
            vol = 0.0
        if vol <= 0:
            continue
        filtered.append(t)

    filtered.sort(key=lambda x: float(x.get("volume24h", 0)), reverse=True)
    return filtered[:TOP_N_SYMBOLS]


def get_klines(symbol: str, interval: str, limit: int = 200) -> list[list[Any]]:
    """
    Fetch candlestick (kline) data from Bybit.

    Args:
        symbol: Trading pair, e.g., "BTCUSDT".
        interval: Bybit interval string, e.g., "60" for 1H, "15" for 15M.
        limit: Number of candles to retrieve (max 1000).

    Returns:
        List of klines. Bybit returns them oldest-first, so the last element
        is the most recent candle. Each candle:
        [startTime, open, high, low, close, volume, turnover]
    """
    payload = _request(
        "/v5/market/kline",
        params={
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        },
    )
    return payload["result"]["list"]


def get_ticker(symbol: str) -> dict[str, Any]:
    """
    Fetch the current ticker for a single symbol.

    Returns:
        Ticker dict with lastPrice, fundingRate, openInterest, etc.
    """
    payload = _request(
        "/v5/market/tickers",
        params={"category": "linear", "symbol": symbol},
    )
    return payload["result"]["list"][0]


def get_funding_rate(symbol: str) -> dict[str, Any]:
    """
    Fetch the current funding rate and OI for a symbol from the tickers endpoint.

    Returns:
        Dict with fundingRate and openInterest (as floats).
    """
    ticker = get_ticker(symbol)
    fr = float(ticker["fundingRate"]) if ticker.get("fundingRate") else 0.0
    oi = float(ticker["openInterest"]) if ticker.get("openInterest") else 0.0
    return {"fundingRate": fr, "openInterest": oi}


def get_open_interest(symbol: str) -> float:
    """
    Fetch the current open interest for a symbol.

    Returns:
        Open interest as a float (number of contracts).
    """
    ticker = get_ticker(symbol)
    return float(ticker["openInterest"]) if ticker.get("openInterest") else 0.0
