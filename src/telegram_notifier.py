import logging
from typing import Any

import requests

from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning(
            "Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env"
        )
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            logger.error(f"Telegram API error: {data}")
            return False
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def format_telegram_signal(signal: dict[str, Any]) -> str:
    ind = signal["indicators"]
    entry = signal["entry_zone"]

    direction_emoji = "📈" if signal["direction"] == "LONG" else "📉"

    conf = signal["confidence"]
    conf_emoji = {"HIGH": "✅", "MEDIUM": "⚠️", "LOW": "❌"}.get(conf, "❓")

    macd_text = (
        ind["macd_15m"].capitalize()
        if ind["macd_15m"] != "none"
        else "No cross"
    )

    fund_status = "⚠️" if "crowded" in ind["funding_context"].lower() else "✅"

    lines = [
        "🚨 NEW SIGNAL",
        "",
        f"Symbol:     {signal['symbol']}",
        f"Direction:  {direction_emoji} {signal['direction']}",
        f"Confidence: {conf_emoji} {conf}",
        "",
        f"Entry Zone: {entry['low']} - {entry['high']}",
        f"Stop Loss:  {signal['stop_loss']}",
        f"Take Profit: {signal['take_profit']}",
        "",
        f"Risk: {signal['suggested_risk_pct']}%  |  R:R: 1:{signal['risk_reward']:.1f}",
        "",
        f"Trend (1H):  {ind['trend_1h']}",
        f"Momentum:    RSI {ind['rsi_pullback']} -> {ind['rsi_15m']} "
        f"({ind['rsi_context']}), MACD {macd_text}",
        f"Funding/OI:  {ind['funding_context']} {fund_status}",
        f"Open Interest: {ind['open_interest']:,.0f}",
        "",
        f"Generated: {signal['generated_at']}",
    ]

    return "\n".join(lines)


def send_signal_alert(signal: dict[str, Any]) -> bool:
    text = format_telegram_signal(signal)
    return send_telegram_message(text)
