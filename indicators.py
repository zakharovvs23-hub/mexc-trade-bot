"""
Расчёт индикаторов: RSI, MACD, объём, Elder-ray (Bull Power / Bear Power).
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
    закрытия дневных свечей. Смотрим именно на закрытие, а не на хвост
    свечи — так советует сам Гудман: где цена закрылась, важнее, чем
    как она металась внутри дня.
    """
    if len(df_d) < period + 2:
        return {"range_high": None, "breakout": False, "dist_from_breakout_pct": None}
    prior_high = df_d["close"].iloc[-(period + 1):-1].max()
    last_close = df_d["close"].iloc[-1]
    breakout = last_close > prior_high
    dist_pct = round((last_close - prior_high) / prior_high * 100, 2) if prior_high else None
    return {
        "range_high": round(float(prior_high), 6),
        "breakout": breakout,
        "dist_from_breakout_pct": dist_pct,
    }
