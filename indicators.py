"""
Расчёт индикаторов: RSI, MACD, объём, Elder-ray (Bull Power / Bear Power,
плюс экспериментальный "ранний вход" по близости Bear Power к нулю),
внутридневное движение.
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

EARLY_BEAR_POWER_PCT = 3.0  # см. calc_elder_ray: порог "ранний вход" методики (2026-09-25)

def calc_elder_ray(df_d: pd.DataFrame, period: int = 13) -> dict:
    """
    Screen 2 (разворот Bear Power) + Screen 3 (добавлен 2026-09-08).

    early_trigger (добавлено 2026-09-25, методика "ранний вход", ЭКСПЕРИМЕНТ) —
    отдельное, более мягкое условие Screen 2: не ждём полноценного разворота
    (bear_power_rising), а входим, как только Bear Power подошёл вплотную к нулю
    (в пределах EARLY_BEAR_POWER_PCT % от цены закрытия), при Bull Power > 0.
    Идея выросла из реального случая (JST, 24.09.2026): бот показывал WAIT
    (bear_power_rising ещё не сработал), но Bear Power был уже практически на
    нуле — сделка вручную зашла раньше формального сигнала и закрылась в плюс.
    Проверено бэктестом на 10 альтах (HYPE/XMR/APT/SUI/FIL/VVV/JST/AERO/ZEC/TIA,
    порог 3%): 24 сделки, 66.7% win-rate, +67% суммарно — сопоставимо с быстрой
    методикой. Именно поэтому статус — экспериментальный, а не основной: одна
    живая сделка не статистика, а бэктест сделан один раз и не на реальных
    ордерах.

    Классический Triple Screen Элдора состоит из трёх экранов, а не двух:
    Screen 1 — недельный тренд, Screen 2 — дневной осциллятор, показывающий
    откат, Screen 3 — точный вход: ордер на покупку чуть выше максимума
    предыдущего дня, который срабатывает только при реальном подтверждении
    ценой, а не сразу по развороту индикатора (у Элдора — трейлинг buy stop
    внутри дня; здесь, без внутридневных данных, берём дневной аналог —
    закрытие выше хая предыдущего дня). Раньше Screen 2 сам был сигналом BUY.

    Ретротест на реальных данных VVV (07.09.2026, сделка со стопом): в этом
    конкретном случае screen3_confirm не изменил бы исход — цена подтвердила
    разворот в тот же день, что сработал Screen 2 (сильный дневной свечной
    рост сразу дал и разворот Bear Power, и закрытие выше вчерашнего хая).
    То есть это НЕ доказанное исправление прошлых убытков, а честно
    добавленный недостающий кусок методики Элдора — защита от случаев, когда
    индикатор разворачивается, а цена ещё не подтвердила это движением
    (отдельный, более распространённый сценарий, который в наших 4 сделках
    пока не встретился).
    """
    if len(df_d) < period + 2:
        return {
            "bull_power": None, "bear_power": None, "bear_power_prev": None,
            "bear_power_rising": False, "screen2_trigger": False,
            "screen3_confirm": False, "prev_high": None,
            "bear_power_pct": None, "early_trigger": False,
        }
    ema = df_d["close"].ewm(span=period, adjust=False).mean()
    bull_power = df_d["high"] - ema
    bear_power = df_d["low"] - ema
    bp_last = float(bull_power.iloc[-1])
    brp_last = float(bear_power.iloc[-1])
    brp_prev = float(bear_power.iloc[-2])
    bear_power_rising = brp_last > brp_prev
    screen2_trigger = (brp_last < 0) and bear_power_rising and (bp_last > 0)

    today_close = float(df_d["close"].iloc[-1])
    prev_high = float(df_d["high"].iloc[-2])
    screen3_confirm = today_close > prev_high

    bear_power_pct = (brp_last / today_close * 100) if today_close else None
    early_trigger = (
        brp_last < 0 and bear_power_pct is not None
        and bear_power_pct >= -EARLY_BEAR_POWER_PCT and bp_last > 0
    )

    return {
        "bull_power": round(bp_last, 6),
        "bear_power": round(brp_last, 6),
        "bear_power_prev": round(brp_prev, 6),
        "bear_power_rising": bear_power_rising,
        "screen2_trigger": screen2_trigger,
        "screen3_confirm": screen3_confirm,
        "prev_high": round(prev_high, 6),
        "bear_power_pct": round(bear_power_pct, 2) if bear_power_pct is not None else None,
        "early_trigger": early_trigger,
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
