"""
Расчёт индикаторов: RSI, MACD, объём, Elder-ray (Bull Power / Bear Power),
пробойный сигнал (методика Гудмана), внутридневное движение.
Используем pandas для скользящих средних.
"""

import pandas as pd

def klines_to_df(klines) -> pd.DataFrame:
    df = pd.DataFrame(klines)
    return df

def calc_rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 1)

def calc_macd(closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    hist_last = histogram.iloc[-1]
    hist_prev = histogram.iloc[-2]
    slope = "вверх" if hist_last > hist_prev else "вниз"
    return {
        "histogram": round(float(hist_last), 6),
        "histogram_prev": round(float(hist_prev), 6),
        "slope": slope,
        "rising": hist_last > hist_prev,
        "trend_up": hist_last > 0,
    }

def calc_volume_signal(volumes: pd.Series, period: int = 20) -> str:
    avg = volumes.iloc[-period:-1].mean()
    last = volumes.iloc[-1]
    if last > avg * 1.2:
        return "выше среднего"
    elif last < avg * 0.8:
        return "ниже среднего"
    return "средний"

def calc_elder_ray(df_d: pd.DataFrame, period: int = 13) -> dict:
    if len(df_d) < period + 2:
        return {
            "bull_power": None, "bear_power": None, "bear_power_prev": None,
            "bear_power_rising": False, "screen2_trigger": False,
        }
    ema = df_d["close"].ewm(span=period, adjust=False).mean()
    bull_power = df_d["high"] - ema
    bear_power = df_d["low"] - ema
    bp_last = float(bull_power.iloc[-1])
    brp_last = float(bear_power.iloc[-1])
    brp_prev = float(bear_power.iloc[-2])
    bear_power_rising = brp_last > brp_prev
    screen2_trigger = (brp_last < 0) and bear_power_rising and (bp_last > 0)
    return {
        "bull_power": round(bp_last, 6),
        "bear_power": round(brp_last, 6),
        "bear_power_prev": round(brp_prev, 6),
        "bear_power_rising": bear_power_rising,
        "screen2_trigger": screen2_trigger,
    }

def calc_breakout_signal(df_d: pd.DataFrame, period: int = 20) -> dict:
    """
    Пробойный сигнал (методика Гудмана): пробой N-дневного максимума
    закрытия дневных свечей, С ПОДТВЕРЖДЕНИЕМ на следующий день.

    Раньше сигнал срабатывал прямо на самой свече пробоя — а это ровно
    тот момент, когда нельзя отличить настоящее начало движения от
    объёмного выдоха (последней волны покупок перед разворотом), см. кейс
    HYPE 07.09.2026: пробой с объёмом 165.8% от среднего тут же оказался
    ложным. Теперь: уровень пробоя считаем по данным ДО дня пробоя, сам
    пробой фиксируем на предыдущей свече, а сигнал засчитываем, только
    если цена ПОСЛЕ пробоя удержалась выше уровня ещё один день.
    """
    if len(df_d) < period + 3:
        return {
            "range_high": None, "breakout": False, "breakout_confirmed": False,
            "dist_from_breakout_pct": None, "breakout_day_volume_pct": None,
        }

    range_high = df_d["close"].iloc[-(period + 2):-2].max()
    breakout_day_close = df_d["close"].iloc[-2]
    today_close = df_d["close"].iloc[-1]

    breakout_day = breakout_day_close > range_high
    confirmed = breakout_day and today_close > range_high

    vol_avg = df_d["volume"].iloc[-(period + 2):-2].mean()
    breakout_day_volume_pct = round(df_d["volume"].iloc[-2] / vol_avg * 100, 1) if vol_avg else None

    dist_pct = round((today_close - range_high) / range_high * 100, 2) if range_high else None

    return {
        "range_high": round(float(range_high), 6),
        "breakout": bool(breakout_day),
        "breakout_confirmed": bool(confirmed),
        "dist_from_breakout_pct": dist_pct,
        "breakout_day_volume_pct": breakout_day_volume_pct,
    }

def calc_intraday_change(df_d: pd.DataFrame, price: float):
    """
    Изменение цены с начала текущей (ещё формирующейся) дневной свечи, в %.
    Чисто информационная метрика (не влияет на signal_type ни в одной из
    методик) — добавлена по просьбе пользователя, чтобы видеть, не входим
    ли мы в монету, которая уже сильно выросла за сегодня (погоня за
    движением), и наоборот — не падает ли она прямо сейчас внутри дня
    (риск почти сразу улететь в стоп-лосс).
    """
    if len(df_d) < 1:
        return None
    today_open = df_d["open"].iloc[-1]
    if not today_open:
        return None
    return round((price - today_open) / today_open * 100, 2)
