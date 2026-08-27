import pandas as pd
from indicators import calc_rsi, calc_macd, calc_volume_signal
from mexc_api import get_klines, get_current_price
from fear_greed import get_fear_greed, fear_greed_note


def analyze_raw(coin: str) -> dict:
    """
    Считает все индикаторы и итоговый сигнал по монете,
    возвращает словарь с данными (без форматирования текста).
    Используется и для одиночного анализа (analyze), и для
    массового сканирования (scanner.py).
    """
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
        fg_warning = "\n⚠️ Рынок в экстремальной жадности — сигнал слабее обычного, возьми меньший объём"
    elif fg_value is not None and fg_value <= 25 and signal_type == "WAIT":
        fg_warning = "\n👀 Рынок в экстремальном страхе — присмотрись, но жди подтверждения тренда"

    # --- Score для ранжирования при массовом сканировании ---
    # BUY-сигналы всегда выше WAIT. Внутри BUY: чем глубже откат (ниже RSI), тем сильнее сигнал.
    # Внутри WAIT: чем ближе RSI к перекупленности и чем хуже недельный тренд, тем слабее сигнал.
    if signal_type == "BUY":
        score = 100 - daily_rsi
        if daily_macd["trend_up"]:
            score += 10
        if fg_value is not None and fg_value >= 75:
            score -= 15
    else:
        score = daily_rsi - 100
        if not weekly_macd["trend_up"]:
            score -= 20

    return {
        "coin": coin.upper(),
        "price": price,
        "weekly_macd": weekly_macd,
        "daily_macd": daily_macd,
        "daily_rsi": daily_rsi,
        "volume_note": volume_note,
        "fg_value": fg_value,
        "fg_note": fg_note,
        "fg_warning": fg_warning,
        "weekly_trend_text": weekly_trend_text,
        "signal_type": signal_type,
        "reasoning": reasoning,
        "score": round(score, 2),
    }


def analyze(coin: str) -> str:
    """Форматированный текстовый анализ по одной монете (как раньше)."""
    d = analyze_raw(coin)

    lines = []
    lines.append(f"📊 {d['coin']} — анализ")
    lines.append(f"Текущая цена: {d['price']}")
    lines.append("")
    lines.append(f"Недельный тренд: {d['weekly_trend_text']} (MACD-гистограмма {d['weekly_macd']['slope']})")
    lines.append(f"Дневной: RSI {d['daily_rsi']}, MACD-гистограмма {d['daily_macd']['slope']}")
    lines.append(f"Объём: {d['volume_note']}")
    lines.append(f"Фон: Fear & Greed {d['fg_note']}")
    lines.append("")

    if d["signal_type"] == "BUY":
        entry = round(d["price"], 6)
        sl = round(entry * 0.97, 6)
        tp = round(entry * 1.06, 6)
        lines.append("💡 СИГНАЛ: Buy")
        lines.append(f"Вход: {entry}")
        lines.append(f"Stop-Loss: {sl} (-3.0%)")
        lines.append(f"Take-Profit: {tp} (+6.0%)")
        lines.append("R/R: 1:2")
        lines.append("")
        lines.append("Обоснование: " + "; ".join(d["reasoning"]))
    else:
        lines.append("💡 СИГНАЛ: Ждать")
        lines.append("Обоснование: " + "; ".join(d["reasoning"]))

    if d["fg_warning"]:
        lines.append(d["fg_warning"])

    return "\n".join(lines)
