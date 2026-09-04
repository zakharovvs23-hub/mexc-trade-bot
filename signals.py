import pandas as pd

from indicators import calc_rsi, calc_macd, calc_volume_signal, calc_elder_ray, calc_breakout_signal
from mexc_api import get_klines, get_current_price
from fear_greed import get_fear_greed, fear_greed_note

FLAT_MA_THRESHOLD_PCT = 1.0  # изменение MA60 за 6 недель, ниже которого тренд считается плоским
BREAKOUT_VOLUME_THRESHOLD_PCT = 120.0  # объём должен быть >=120% среднего, чтобы пробой засчитался
BREAKOUT_MAX_CHASE_PCT = 5.0  # не гнаться, если цена ушла дальше 5% от уровня пробоя


def calc_weekly_ma_trend(weekly_close: pd.Series, price: float) -> dict:
    """
    Screen 1 (недельный, Elder's Triple Screen) — классифицирует тренд по MA10/MA30/MA60.
    Бычий ("Tier A") — цена выше MA10 выше MA30 выше MA60 (правильный порядок,
    а не просто "цена выше каждой по отдельности"), и MA60 подтверждённо растёт
    (>1% за 6 недель).
    Медвежий — цена ниже MA60, и MA60 подтверждённо падает.
    Боковик — всё остальное (MA плоские, смешанный порядок, ранние признаки смены тренда).
    """
    if len(weekly_close) < 66:
        return {"trend": "Недостаточно данных", "ma10": None, "ma30": None, "ma60": None, "ma60_slope_pct": None}

    ma10 = weekly_close.rolling(10).mean()
    ma30 = weekly_close.rolling(30).mean()
    ma60 = weekly_close.rolling(60).mean()

    ma10_now = ma10.iloc[-1]
    ma30_now = ma30.iloc[-1]
    ma60_now = ma60.iloc[-1]
    ma60_prev = ma60.iloc[-7]  # 6 недель назад
    ma60_slope_pct = (ma60_now - ma60_prev) / ma60_prev * 100

    bull_order = price > ma10_now > ma30_now > ma60_now

    if bull_order and ma60_slope_pct > FLAT_MA_THRESHOLD_PCT:
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
    Считает Elder's Triple Screen целиком и итоговый сигнал по монете,
    возвращает словарь с данными (без форматирования текста).
    Используется и для одиночного анализа (analyze), и для
    массового сканирования пулом (scanner.py).

    Две независимые методики входа, обе используют общий Screen 1 (недельный тренд):
    - signal_type — методика Элдора (откат Bear Power внутри уже бычьей структуры).
    - signal_type_breakout — методика Гудмана (пробой 20-дневного максимума
      закрытия с подтверждением объёмом, без погони за уже ушедшей ценой).
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
    elder = calc_elder_ray(df_d)
    breakout = calc_breakout_signal(df_d)
    fg_value, fg_class = get_fear_greed()
    fg_note = fear_greed_note(fg_value, fg_class)
    ma_trend = calc_weekly_ma_trend(df_w["close"], price)

    weekly_trend_text = "вверх" if weekly_macd["trend_up"] else "вниз"

    tier_a = weekly_macd["rising"] and ma_trend["trend"] == "Бычий"

    reasoning = []
    needs_review = False

    if tier_a:
        if elder["screen2_trigger"]:
            signal_type = "BUY"
            reasoning.append(
                "Screen 1: недельный тренд Бычий (MACD-гистограмма растёт, MA10>MA30>MA60). "
                "Screen 2: Bear Power отрицательный, но разворачивается вверх при Bull Power > 0 — точка входа."
            )
        else:
            signal_type = "WATCH"
            reasoning.append(
                "Screen 1: недельный тренд Бычий — структура готова к покупке. "
                "Screen 2 ещё не сработал: ждём разворота Bear Power (сейчас "
                f"{elder['bear_power']}, {'растёт' if elder['bear_power_rising'] else 'падает'})."
            )
    elif ma_trend["trend"] == "Медвежий" or not weekly_macd["trend_up"]:
        signal_type = "WAIT"
        reasoning.append("Недельный тренд не бычий — на споте покупки не рассматриваем (правило 18)")
    else:
        signal_type = "WAIT"
        needs_review = True
        reasoning.append("Недельный тренд — боковик/переходный: сигнала нет, требует ручной проверки")

    breakout_reasoning = []
    if tier_a:
        volume_ok = volume_ratio_pct is not None and volume_ratio_pct >= BREAKOUT_VOLUME_THRESHOLD_PCT
        if breakout["breakout"] and volume_ok:
            if breakout["dist_from_breakout_pct"] is not None and breakout["dist_from_breakout_pct"] <= BREAKOUT_MAX_CHASE_PCT:
                signal_type_breakout = "BUY"
                breakout_reasoning.append(
                    f"Пробой 20-дневного максимума закрытия ({breakout['range_high']}), "
                    f"объём {volume_ratio_pct}% от среднего — подтверждён."
                )
            else:
                signal_type_breakout = "WATCH"
                breakout_reasoning.append(
                    f"Пробой уже был ({breakout['range_high']}), но цена ушла на "
                    f"{breakout['dist_from_breakout_pct']}% — не гонимся, ждём следующей консолидации."
                )
        else:
            signal_type_breakout = "WATCH"
            breakout_reasoning.append("Screen 1 бычий, но пробоя диапазона ещё не было — в поле зрения.")
    else:
        signal_type_breakout = "WAIT"
        breakout_reasoning.append("Недельный тренд не бычий — пробойную методику не рассматриваем.")

    if signal_type in ("BUY", "WATCH") and daily_rsi > 70:
        reasoning.append(f"⚠️ RSI {daily_rsi} — перекуплен, возможна дивергенция, проверь дневной график глазами")

    fg_warning = ""
    if fg_value is not None and fg_value >= 75 and signal_type == "BUY":
        fg_warning = "\n⚠️ Рынок в экстремальной жадности — сигнал слабее обычного, возьми меньше объём"
    elif fg_value is not None and fg_value <= 25 and signal_type in ("BUY", "WATCH"):
        fg_warning = "\n👀 Рынок в экстремальном страхе — присмотрись, но жди подтверждения тренда"

    if signal_type == "BUY":
        score = 200 - daily_rsi
        if weekly_macd["rising"]:
            score += 10
        if fg_value is not None and fg_value >= 75:
            score -= 15
    elif signal_type == "WATCH":
        score = 100 - daily_rsi
    else:
        score = daily_rsi - 100

    if not weekly_macd["trend_up"]:
        score -= 20
    if needs_review:
        score -= 5

    return {
        "coin": coin.upper(),
        "price": price,
        "weekly_macd": weekly_macd,
        "daily_macd": daily_macd,
        "daily_rsi": daily_rsi,
        "volume_note": volume_note,
        "volume_ratio_pct": volume_ratio_pct,
        "sr_levels": sr_levels,
        "elder": elder,
        "breakout": breakout,
        "fg_value": fg_value,
        "fg_note": fg_note,
        "fg_warning": fg_warning,
        "weekly_trend_text": weekly_trend_text,
        "ma_trend": ma_trend,
        "tier_a": tier_a,
        "signal_type": signal_type,
        "signal_type_breakout": signal_type_breakout,
        "needs_review": needs_review,
        "reasoning": reasoning,
        "breakout_reasoning": breakout_reasoning,
        "score": round(score, 2),
    }


def analyze(coin: str) -> str:
    """Форматированный текстовый анализ по одной монете — вся та же информация,
    которую видит Вадим при ручной проверке: Screen 1, Screen 2 (обе методики), RSI, объём, S/R, фон."""
    d = analyze_raw(coin)
    lines = []
    lines.append(f"📊 {d['coin']} — анализ")
    lines.append(f"Текущая цена: {d['price']}")
    lines.append("")
    lines.append(f"Screen 1 (недельный MACD): {d['weekly_trend_text']} ({d['weekly_macd']['slope']})")
    lines.append(
        f"Screen 1 (недельный MA-тренд): {d['ma_trend']['trend']}"
        + (f", MA60 наклон {d['ma_trend']['ma60_slope_pct']}% за 6 нед." if d['ma_trend']['ma60_slope_pct'] is not None else "")
    )

    e = d["elder"]
    if e["bull_power"] is not None:
        lines.append(
            f"Screen 2 (Elder-ray): Bull Power {e['bull_power']}, Bear Power {e['bear_power']}"
            f" ({'растёт' if e['bear_power_rising'] else 'падает'})"
        )

    lines.append(f"Дневной RSI (вторично): {d['daily_rsi']}")

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
        lines.append("💡 СИГНАЛ (методика Элдора, откат): Buy")
        lines.append(f"Вход: {entry}")
        lines.append(f"Stop-Loss: {sl} (-3.0%)")
        lines.append(f"Take-Profit: {tp} (+6.0%)")
        lines.append("R/R: 1:2")
    elif d["signal_type"] == "WATCH":
        lines.append("💡 СИГНАЛ (методика Элдора, откат): Следить (структура бычья, входа ещё нет)")
    else:
        lines.append("💡 СИГНАЛ (методика Элдора, откат): Ждать")

    lines.append("Обоснование: " + "; ".join(d["reasoning"]))
    if d["fg_warning"]:
        lines.append(d["fg_warning"])

    lines.append("")
    lines.append(f"💡 СИГНАЛ (методика Гудмана, пробой): {d['signal_type_breakout']}")
    lines.append("Обоснование: " + "; ".join(d["breakout_reasoning"]))

    return "\n".join(lines)
