"""
Получение данных с публичного MEXC Spot API.
Документация: https://mexcdevelop.github.io/apidocs/spot_v3_en/
Ключ API не нужен - все эндпоинты ниже публичные (только чтение).
"""
import requests

BASE_URL = "https://api.mexc.com"

# Соответствие наших таймфреймов интервалам MEXC
INTERVALS = {
    "1h": "60m",
    "1d": "1d",
    "1w": "1W",
}


def normalize_symbol(coin: str) -> str:
    """ADA -> ADAUSDT, ada -> ADAUSDT, adausdt -> ADAUSDT"""
    coin = coin.strip().upper()
    if not coin.endswith("USDT"):
        coin = coin + "USDT"
    return coin


def get_current_price(coin: str) -> float:
    symbol = normalize_symbol(coin)
    r = requests.get(f"{BASE_URL}/api/v3/ticker/price", params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    data = r.json()
    return float(data["price"])


def get_klines(coin: str, timeframe: str, limit: int = 100):
    """
    Возвращает список свечей: [open_time, open, high, low, close, volume, ...]
    timeframe: "1h", "1d", "1w"
    """
    symbol = normalize_symbol(coin)
    interval = INTERVALS[timeframe]
    r = requests.get(
        f"{BASE_URL}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=10,
    )
    r.raise_for_status()
    raw = r.json()
    klines = []
    for row in raw:
        klines.append({
            "open_time": row[0],
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        })
    return klines
