"""
Массовое сканирование монет пулом: анализ сразу нескольких тикеров одним запросом,
с сортировкой от самых сильных сигналов к самым слабым (BUY > WATCH > WAIT).
Плюс — зашитые фиксированные пулы ротации (A-E, 199 тикеров), чтобы можно было
сканировать их прямо из бота командой /pool, без необходимости присылать список
тикеров вручную каждый раз.
"""

from signals import analyze_raw

TOP_COINS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "TON", "AVAX", "LINK",
    "DOT", "TRX", "MATIC", "LTC", "SHIB", "BCH", "NEAR", "UNI", "ATOM", "ETC",
    "XLM", "FIL", "APT", "ARB", "OP", "SUI", "INJ", "IMX", "RUNE", "AAVE",
    "MKR", "SAND", "MANA", "GRT", "ALGO", "FTM", "EGLD", "XTZ", "THETA", "EOS",
    "AXS", "KAVA", "CHZ", "1INCH", "ZEC", "COMP", "SNX", "CRV", "ENJ", "GALA",
]

POOLS = {
    "a": [
        "C98", "SKL", "WOO", "DODO", "STORJ", "TIA", "SEI", "STRK", "JUP", "PYTH",
        "JTO", "W", "ONDO", "RENDER", "WLD", "KAS", "ENA", "MNT", "HYPE", "BERA",
        "CRO", "OKB", "BGB", "AERO", "JASMY", "EIGEN", "VIRTUAL", "MOVE", "ME", "ORDI",
        "ATH", "VELO", "BTC", "ETH", "BNB", "XRP", "SOL", "TRX", "ZEC", "DOGE",
        "XMR", "LINK",
    ],
    "b": [
        "ADA", "XLM", "BCH", "CC", "LTC", "UNI", "HBAR", "AVAX", "SHIB", "SUI",
        "TAO", "NEAR", "AAVE", "ASTER", "SKY", "PEPE", "DOT", "MORPHO", "ICP", "ETC",
        "PI", "POL", "KCS", "LIT", "VVV", "JST", "ATOM", "ALGO", "QNT", "ARB",
        "DASH", "TRUMP", "CAKE", "FLR", "XDC", "VET", "GRAM", "U", "FIL", "CRV",
        "ETHFI", "NEXO", "INJ", "STX", "APT", "ZRO",
    ],
    "c": [
        "FET", "MON", "BSV", "SUN", "FF", "GALA", "ROSE", "ANKR", "CELO", "BAT",
        "ZIL", "ONE", "HOT", "RVN", "WAVES", "KSM", "MINA", "FLOW", "XEC", "NEO",
        "QTUM", "IOST", "ZRX", "BAL", "YFI", "SUSHI", "RSR", "CFX", "TWT", "LDO",
        "APE", "BLUR", "ID", "MASK", "GLMR", "BEAM", "DYDX", "RUNE", "IMX", "SYS",
        "PROM", "COTI", "VTHO", "MANA", "XVS", "ARPA", "WAXP", "CELR", "ELF", "TKO",
    ],
    "d": [
        "DUSK", "CHZ", "DATA", "SC", "RAY", "POND", "CVX", "KAVA", "ZEN", "CKB",
        "IOTX", "HIGH", "API3", "XVG", "YGG", "ASTR", "NMR", "FARM", "OGN", "IOTA",
        "GMX", "RLC", "CHR", "UMA", "POLYX", "OSMO", "RPL", "CTK", "LPT", "ILV",
        "FIDA", "ONG", "ONT", "TFUEL", "COMP", "ACE", "ZK", "LISTA", "1INCH", "METIS",
        "KNC", "SAGA",
    ],
    "e": [
        "OP", "XAI", "BANANA", "KAIA", "AXS", "AEVO", "DYM", "TAIKO", "IO", "XTZ",
        "WEMIX", "SNX", "MANTA", "SAND", "STG", "LRC", "NFP", "ENJ", "PENDLE",
    ],
}


def scan_coins(coins: list[str]) -> tuple[list[dict], list[tuple[str, str]]]:
    """
    Прогоняет analyze_raw (Elder's Triple Screen + методика Гудмана) по списку монет
    и сортирует результаты по score. Монеты, по которым не удалось получить данные,
    попадают в errors, а не прерывают сканирование остальных.
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


_SIGNAL_EMOJI = {"BUY": "🟢", "WATCH": "🟡", "WAIT": "⚪️"}


def format_scan_result(results: list[dict], errors: list[tuple], title: str | None = None) -> str:
    """
    Пулом выводит по каждой монете два сигнала (Элдор/Гудман), цену, недельный тренд,
    Elder-ray, RSI и score.
    """
    if not results and not errors:
        return "Нет данных для отображения."

    buy_count = sum(1 for d in results if d["signal_type"] == "BUY")
    watch_count = sum(1 for d in results if d["signal_type"] == "WATCH")
    buy_count_bo = sum(1 for d in results if d["signal_type_breakout"] == "BUY")

    header = title or f"Результаты сканирования ({len(results)} монет)"

    lines = [
        f"📊 {header}, от сильных к слабым: 🟢 BUY(Элдор) {buy_count} | 🟡 WATCH(Элдор) {watch_count} "
        f"| 🟢 BUY(Гудман) {buy_count_bo}",
        "",
    ]

    for i, d in enumerate(results, start=1):
        emoji = _SIGNAL_EMOJI.get(d["signal_type"], "⚪️")
        emoji_bo = _SIGNAL_EMOJI.get(d["signal_type_breakout"], "⚪️")
        e = d["elder"]

        elder_bit = ""
        if d["signal_type"] == "WATCH" and e["bear_power"] is not None:
            elder_bit = f" | BearPower {e['bear_power']}{'↑' if e['bear_power_rising'] else '↓'}"

        lines.append(
            f"{i}. {emoji}Элдор/{emoji_bo}Гудман {d['coin']} — {d['signal_type']}/{d['signal_type_breakout']} "
            f"| цена {d['price']} | недельный {d['ma_trend']['trend']} | RSI {d['daily_rsi']}"
            f"{elder_bit} | score {d['score']:.1f}"
        )

    if watch_count:
        lines.append("")
        lines.append(
            "🟡 WATCH (Элдор) — недельная структура уже бычья (Screen 1 пройден), но точка входа "
            "(разворот Bear Power) ещё не наступила. Стоит держать в поле зрения на "
            "следующих сканах — именно такие монеты раньше терялись между сканами."
        )

    if errors:
        lines.append("")
        lines.append("⚠️ Не удалось получить данные по: " + ", ".join(c for c, _ in errors))

    return "\n".join(lines)
