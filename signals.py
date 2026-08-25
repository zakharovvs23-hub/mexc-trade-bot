import pandas as pd
from indicators import calc_rsi, calc_macd, calc_volume_signal
from mexc_api import get_klines, get_current_price
from fear_greed import get_fear_greed, fear_greed_note


def analyze(coin: str) -> str:
    price = get_current_price(coin)

    weekly = get_klines(coin, "1w", limit=60)
    daily = get_klines(coin, "1d", limit=60)

    df_w = pd.DataFrame(weekly)
    df_d = pd.DataFrame(daily)

    weekly_macd = calc_macd(df_w["close"])
    daily_macd = calc_macd(df_d["close"])
    daily_rsi = calc_rsi(df_d["close"])
    volume_note = calc_volume_signal(df_d["volume"])

    fg_value, fg_class = get_fear_greed()
    fg_note = fear_greed_note(fg_value, fg_class)

    weekly_trend_text = "вверх" if weekly_macd["trend_up"] else "вниз"

    # --- Правило 18: сначала недельный тренд (Triple Screen, первый экран) ---
    # --- Логика сигнала (спот, только покупки) ---
    signal_type = None
    reasoning = []

    if weekly_macd["trend_up"]:
        if daily_rsi < 50:
            signal_type = "BUY"
            reasoning.append("недельный тренд вверх, дневной откат (RSI < 50) — подходящая точка входа")
        else:
            signal_type = "WAIT"
            reasoning.append("недельный тренд вверх, но дневной RSI высокий — ждём отката")
    else:
        signal_type = "WAIT"
        reasoning.append("недельный тренд вниз — на споте покупки не рассматриваем (правило 18)")

    # Фон Fear & Greed как модификатор (правило 17), не меняет тип сигнала, только предупреждение
    fg_warning = ""
    if fg_value is not None and fg_value >= 75 and signal_type == "BUY":
        fg_warning = "\n⚠️ Рынок в экстремальной жадности — сигнал слабее обычного, возьми меньшим объёмом или пропусти."
    elif fg_value is not None and fg_value <= 25 and signal_type == "WAIT":
        fg_warning = "\n👀 Рынок в экстремальном страхе — присмотрись, но жди подтверждения тренда."

    lines = []
    lines.append(f"📊 {coin.upper()} — анализ")
    lines.append(f"Текущая цена: {price}")
    lines.append("")
    lines.append(f"Недельный тренд: {weekly_trend_text} (MACD-гистограмма {weekly_macd['slope']})")
    lines.append(f"Дневной: RSI {daily_rsi}, MACD-гистограмма {daily_macd['slope']}")
    lines.append(f"Объём: {volume_note}")
    lines.append(f"Фон: Fear & Greed {fg_note}")
    lines.append("")

    if signal_type == "BUY":
        entry = round(price, 6)
        sl = round(entry * 0.97, 6)   # -3%
        tp = round(entry * 1.06, 6)   # +6%, R/R 1:2
        lines.append("💡 СИГНАЛ: Buy")
        lines.append(f"Вход: {entry}")
        lines.append(f"Stop-Loss: {sl} (-3.0%)")
        lines.append(f"Take-Profit: {tp} (+6.0%)")
        lines.append("R/R: 1:2")
        lines.append("")
        lines.append("Обоснование: " + "; ".join(reasoning))
    else:
        lines.append("💡 СИГНАЛ: Ждать")
        lines.append("Обоснование: " + "; ".join(reasoning))

    if fg_warning:
        lines.append(fg_warning)

    return "\n".join(lines)
