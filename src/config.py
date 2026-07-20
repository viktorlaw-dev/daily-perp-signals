"""
Loads configuration from environment variables.

This keeps secrets (API keys, Telegram tokens) out of the codebase.
Values are read from a .env file in the project root.
"""

import os
from pathlib import Path
from dotenv import load_dotenv


def load_env() -> None:
    """Load environment variables from the .env file at the project root."""
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    load_dotenv(dotenv_path=env_path)


def get_env_var(name: str, required: bool = False) -> str | None:
    """
    Retrieve an environment variable.

    Args:
        name: The environment variable name.
        required: If True, raise an error when the variable is missing.

    Returns:
        The value of the variable, or None if not required and missing.
    """
    value = os.getenv(name)
    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


# Load .env immediately when this module is imported.
load_env()

# Bybit Testnet credentials.
# These are optional for public market-data endpoints, but including them
# can improve rate-limit allowances.
BYBIT_API_KEY = get_env_var("BYBIT_API_KEY")
BYBIT_API_SECRET = get_env_var("BYBIT_API_SECRET")

# Telegram credentials.
TELEGRAM_BOT_TOKEN = get_env_var("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = get_env_var("TELEGRAM_CHAT_ID")

# Bybit base URL.
# Use mainnet for live signals. Testnet blocks cloud IP ranges (GitHub Actions, etc.).
BYBIT_BASE_URL = "https://api.bybit.com"
# For local development on testnet, change to "https://api-testnet.bybit.com"

# Symbol selection settings.
TOP_N_SYMBOLS = 15
MEMECOIN_DENYLIST = {
    "DOGE", "SHIB", "PEPE", "BONK", "WIF", "FLOKI", "BOME", "NEIRO", "MOG", "MEME"
}

# Timeframe strings used by Bybit API.
TIMEFRAME_1H = "60"
TIMEFRAME_15M = "15"

# Strategy parameters — Trend filter (1H).
EMA_FAST_PERIOD = 50
EMA_SLOW_PERIOD = 200

# Strategy parameters — Momentum confirmation (15M).
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# Swing-point lookback for stop-loss placement (number of 15M candles).
SWING_LOOKBACK = 20

# Funding-rate thresholds for confidence weighting.
# Above this absolute value, funding is considered "crowded."
FUNDING_CROWDING_THRESHOLD = 0.001  # 0.1%

# Risk / reward.
DAILY_LOSS_CAP_PCT = -3.0
MAX_RISK_PER_TRADE_PCT = 1.5
TARGET_RR = 1.5  # Tuned: 1.5 gives highest PF (1.72) vs 2.0 (1.33)

# ---- Tuning parameters ----

# If True, a MACD cross is REQUIRED for a signal (not just a confidence boost).
MACD_CROSS_REQUIRED = False  # Tuned: makes results worse

# Cooldown between trades (number of 15M candles).
# 0 = no cooldown.  16 = 4 hours.  32 = 8 hours.
COOLDOWN_CANDLES = 0  # Tuned: cooldown reduces overall profitability

# Hard-coded symbol blacklist (backtest exclusions).
# Use base token name without USDT/PERP suffix.
BACKTEST_BLACKLIST: set[str] = {
    "PUMPFUN", "RESOLV", "SOON", "SAND", "STRK",
}

# Auto-filter: exclude symbols with profit factor below this threshold.
# Requires a performance log file to exist.  0 = disabled.
AUTO_FILTER_PF_THRESHOLD = 0.0
