import pandas as pd
from indicators import calc_rsi, calc_macd, calc_volume_signal
from mexc_api import get_klines, get_current_price
from fear_greed import get_fear_greed, fear_greed_note

FLAT_MA_THRESHOLD_PCT = 1.0  # изменение MA60 за 5 недель, ниже которого тренд считается плоским


def calc_weekly_ma_trend(weekly_close: pd.Series, price: float) -> dict:
    """
    Классифицирует недельный тренд по MA10/MA30/MA60 на три категории:
    Бычий — цена выше всех трёх MA, и MA60 подтверждённо растёт.
    Медвежий — цена ниже MA60, и MA60 подтверждённо падает (MA выступает сопротивлением).
    Боковик — всё остальное: MA плоские, цена колеблется без выраженного наклона,
    смешанное расположение относительно MA (в т.ч. ранние признаки смены тренда).
    """
    if len(weekly_close) < 65:
        return {"trend": "Недостаточно данных", "ma10": None, "ma30": None, "ma60": None, "ma60_slope_pct": None}

    ma10 = weekly_close.rolling(10).mean()
    ma30 = weekly_close.rolling(30).mean()
    ma60 = weekly_close.rolling(60).mean()

    ma10_now = ma10.iloc[-1]
    ma30_now = ma30.iloc[-1]
    ma60_now = ma60.iloc[-1]
    ma60_prev = ma60.iloc[-6]  # 5 недель назад
    ma60_slope_pct = (ma60_now - ma60_prev) / ma60_prev * 100

    if price > ma10_now and price > ma30_now and price > ma60_now and ma60_slope_pct > FLAT_MA_THRESHOLD_PCT:
        trend = "Бычий"
    elif price < ma60_now and ma60_slope_pct < -FLAT_MA_THRESHOLD_PCT:
        trend = "Медвежий"
    else:
        trend = "Боковик"

    return {
        "trend": trend,
        "ma10": round(ma10_now, 6),
        "ma30": round(ma30_now, 6),
        "ma60": round(ma60_now, 6),
        "ma60_slope_pct": round(ma60_slope_pct, 2),
    }


def calc_volume_ratio(df_d: pd.DataFrame):
    """Текущий дневной объём в % от среднего объёма за предыдущий период."""
    if len(df_d) < 2:
        return None
    current = df_d["volume"].iloc[-1]
    avg = df_d["volume"].iloc[:-1].mean()
    if not avg:
        return None
    return round(current / avg * 100, 1)


def find_support_resistance(df_d: pd.DataFrame, price: float, window: int = 3) -> dict:
    """
    Простой поиск ближайших уровней поддержки/сопротивления на дневном графике
    методом локальных пиков (high) и впадин (low) за доступный период.
    """
    n = len(df_d)
    resistances = []
    supports = []
    for i in range(window, n - window):
        h = df_d["high"].iloc[i]
        if h == df_d["high"].iloc[i - window:i + window + 1].max():
            resistances.append(h)
        l = df_d["low"].iloc[i]
        if l == df_d["low"].iloc[i - window:i + window + 1].min():
            supports.append(l)

    resistances_above = [r for r in resistances if r > price]
    supports_below = [s for s in supports if s < price]

    nearest_resistance = min(resistances_above) if resistances_above else None
    nearest_support = max(supports_below) if supports_below else None

    return {
        "resistance": round(nearest_resistance, 6) if nearest_resistance is not None else None,
        "resistance_dist_pct": round((nearest_resistance - price) / price * 100, 2) if nearest_resistance is not None else None,
        "support": round(nearest_support, 6) if nearest_support is not None else None,
        "support_dist_pct": round((price - nearest_support) / price * 100, 2) if nearest_support is not None else None,
    }


def analyze_raw(coin: str) -> dict:
    """
    Считает все индикаторы и итоговый сигнал по монете,
    возвращает словарь с данными (без форматирования текста).
    Используется и для одиночного анализа (analyze), и для
    массового сканирования (scanner.py).
    """
    price = get_current_price(coin)

    weekly = get_klines(coin, "1w", limit=110)
    daily = get_klines(coin, "1d", limit=60)

    df_w = pd.DataFrame(weekly)
    df_d = pd.DataFrame(daily)

    weekly_macd = calc_macd(df_w["close"])
    daily_macd = calc_macd(df_d["close"])
    daily_rsi = calc_rsi(df_d["close"])
    volume_note = calc_volume_signal(df_d["volume"])
    volume_ratio_pct = calc_volume_ratio(df_d)
    sr_levels = find_support_resistance(df_d, price)

    fg_value, fg_class = get_fear_greed()
    fg_note = fear_greed_note(fg_value, fg_class)

    weekly_trend_text = "вверх" if weekly_macd["trend_up"] else "вниз"
    ma_trend = calc_weekly_ma_trend(df_w["close"], price)

    # --- Правило 18: сначала недельный тренд (Triple Screen, первый экран) ---
    # --- Логика сигнала (спот, только покупки) ---
    signal_type = None
    reasoning = []
    needs_review = False

    if weekly_macd["trend_up"]:
        if daily_rsi < 50:
            signal_type = "BUY"
            reasoning.append("Недельный тренд вверх, дневной откат (RSI < 50) — подходящая точка")
        else:
            signal_type = "WAIT"
            reasoning.append("Недельный тренд вверх, но дневной RSI высокий — ждём отката")
    else:
        signal_type = "WAIT"
        reasoning.append("Недельный тренд вниз — на споте покупки не рассматриваем (правило 18)")

    # --- Фильтр по недельному MA-тренду ---
    if signal_type == "BUY" and ma_trend["trend"] == "Медвежий":
        signal_type = "WAIT"
        reasoning.append("Тренд против сигнала: недельный MA-тренд медвежий (MA60 выступает сопротивлением)")
    elif signal_type == "BUY" and ma_trend["trend"] == "Боковик":
        needs_review = True
        reasoning.append("Недельный тренд — боковик: сигнал требует доп. проверки на дневном таймфрейме")

    # Фон Fear & Greed как модификатор (правило 17), не меняет тип сигнала, только предупреждение
    fg_warning = ""
    if fg_value is not None and fg_value >= 75 and signal_type == "BUY":
        fg_warning = "\n⚠️ Рынок в экстремальной жадности — сигнал слабее обычного, возьми меньше объём"
    elif fg_value is not None and fg_value <= 25 and signal_type == "WAIT":
        fg_warning = "\n👀 Рынок в экстремальном страхе — присмотрись, но жди подтверждения тренда"

    # --- Score для ранжирования при массовом сканировании ---
    if signal_type == "BUY":
        score = 100 - daily_rsi
        if daily_macd["trend_up"]:
            score += 10
        if fg_value is not None and fg_value >= 75:
            score -= 15
        if needs_review:
            score -= 5
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
        "volume_ratio_pct": volume_ratio_pct,
        "sr_levels": sr_levels,
        "fg_value": fg_value,
        "fg_note": fg_note,
        "fg_warning": fg_warning,
        "weekly_trend_text": weekly_trend_text,
        "ma_trend": ma_trend,
        "signal_type": signal_type,
        "needs_review": needs_review,
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
    lines.append(f"Недельный тренд (MACD): {d['weekly_trend_text']}")
    lines.append(f"Недельный тренд (MA): {d['ma_trend']['trend']}")
    lines.append(f"Дневной: RSI {d['daily_rsi']}, MACD-гистограмма {d['daily_macd']['slope']}")
    if d["volume_ratio_pct"] is not None:
        lines.append(f"Объём: {d['volume_note']} ({d['volume_ratio_pct']}% от среднего)")
    else:
        lines.append(f"Объём: {d['volume_note']}")

    sr = d["sr_levels"]
    if sr["resistance"] is not None:
        lines.append(f"Ближайшее сопротивление: {sr['resistance']} (+{sr['resistance_dist_pct']}%)")
    if sr["support"] is not None:
        lines.append(f"Ближайшая поддержка: {sr['support']} (-{sr['support_dist_pct']}%)")

    lines.append(f"Фон: Fear & Greed {d['fg_note']}")
    lines.append("")

    if d["signal_type"] == "BUY":
        entry = round(d["price"], 6)
        sl = round(entry * 0.97, 6)
        tp = round(entry * 1.06, 6)
        lines.append("💡 СИГНАЛ: Buy" + (" ⚠️ требует доп. проверки" if d["needs_review"] else ""))
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
