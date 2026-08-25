"""
Расчёт индикаторов: RSI, MACD, объём.
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
    # Наклон гистограммы: сравниваем последние два значения
    hist_last = histogram.iloc[-1]
    hist_prev = histogram.iloc[-2]
    slope = "вверх" if hist_last > hist_prev else "вниз"
    return {
        "histogram": round(float(hist_last), 6),
        "slope": slope,
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
