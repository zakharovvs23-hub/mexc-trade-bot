"""
Массовое сканирование монет: анализ сразу нескольких тикеров одним запросом,
с сортировкой от самых сильных сигналов к самым слабым.
"""
from signals import analyze_raw

# Список монет по умолчанию, если пользователь не указал свой (топ-50)
TOP_COINS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "TON", "AVAX", "LINK",
    "DOT", "TRX", "MATIC", "LTC", "SHIB", "BCH", "NEAR", "UNI", "ATOM", "ETC",
    "XLM", "FIL", "APT", "ARB", "OP", "SUI", "INJ", "IMX", "RUNE", "AAVE",
    "MKR", "SAND", "MANA", "GRT", "ALGO", "FTM", "EGLD", "XTZ", "THETA", "EOS",
    "AXS", "KAVA", "CHZ", "1INCH", "ZEC", "COMP", "SNX", "CRV", "ENJ", "GALA",
]


def scan_coins(coins: list[str]) -> tuple[list[dict], list[tuple[str, str]]]:
    """
    Прогоняет analyze_raw по списку монет и сортирует результаты по score
    (от самого сильного BUY до самого слабого WAIT).
    Монеты, по которым не удалось получить данные, попадают в errors,
    а не прерывают сканирование остальных.
    """
    results = []
    errors = []
    for coin in coins:
        try:
            results.append(analyze_raw(coin))
        except Exception as e:
            errors.append((coin.upper(), str(e)))
    results.sort(key=lambda d: d["score"], reverse=True)
    return results, errors


def format_scan_result(results: list[dict], errors: list[tuple]) -> str:
    if not results and not errors:
        return "Нет данных для отображения."

    lines = ["📊 Результаты сканирования (от сильных к слабым):", ""]
    for i, d in enumerate(results, start=1):
        emoji = "🟢" if d["signal_type"] == "BUY" else "⚪️"
        lines.append(
            f"{i}. {emoji} {d['coin']} — {d['signal_type']} "
            f"| цена {d['price']} | RSI {d['daily_rsi']} | score {d['score']:.1f}"
        )

    if errors:
        lines.append("")
        lines.append("⚠️ Не удалось получить данные по: " + ", ".join(c for c, _ in errors))

    return "\n".join(lines)
