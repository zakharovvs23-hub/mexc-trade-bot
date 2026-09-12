"""
Бэктест торговой стратегии по историческим дневным данным (~1 год).
Правило теперь совпадает с живой логикой signals.py (Elder's Triple Screen):
покупка при недельном Screen 1 = Бычий (MACD-гистограмма растёт, MA10>MA30>
MA60 растёт >1% за 6 недель) И дневном Screen 2 = Bear Power отрицательный,
но разворачивается вверх при Bull Power > 0. RSI в правило входа не входит —
он вторичное подтверждение, как и в живом сканере.
Выход по Take-Profit, Stop-Loss или по лимиту времени удержания.
Недельная структура для истории приближённо считается ресемплингом дневных
закрытий в 7-дневные свечи (MEXC отдаёт недельные свечи только текущим
окном, а не произвольным диапазоном в прошлом).

Буфер данных увеличен с 90 до 500 дней (08.09.2026) — со старым буфером
запрос всегда возвращал меньше 66 недель после ресемплинга (365+90=455
дней = ~65 недель), поэтому недельный Screen 1 падал с ошибкой
"Недостаточно исторических данных" для ЛЮБОЙ монеты, включая BTC —
дело было не в реальной истории монеты, а в том, что бот просто не
запрашивал её в достаточном объёме.
"""
import pandas as pd
from mexc_api import get_klines

FLAT_MA_THRESHOLD_PCT = 1.0
ELDER_PERIOD = 13

def _weekly_tier_a_series(daily_close: pd.Series, ma_periods: tuple[int, int, int] = (10, 30, 60),
                           slope_lookback_weeks: int = 6, min_weeks: int = 66) -> pd.Series:
    """Screen 1 по каждому дню истории: True, если на тот момент недельная
    структура была Бычий (Tier A) — та же логика, что в signals.calc_weekly_ma_trend
    (строгая версия) или calc_weekly_ma_trend_fast (экспериментальная, MA5/13/20),
    но векторизованная и с недельным MACD-гистограммой в довесок.

    Параметризовано 2026-09-12 (по просьбе Вадима: "сделай бэктест на быструю методику,
    а то мы её ввели для сравнения, а не сравниваем") — раньше периоды MA10/30/60 и
    окно наклона в 6 недель были зашиты жёстко, поддерживалась только строгая методика."""
    weekly = daily_close.resample("7D").last().dropna()
    if len(weekly) < min_weeks:
        raise ValueError(f"Недостаточно исторических данных для недельного Screen 1 (нужно ~{min_weeks} недель)")

    ema_fast = weekly.ewm(span=12, adjust=False).mean()
    ema_slow = weekly.ewm(span=26, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line
    hist_rising = hist > hist.shift(1)

    p1, p2, p3 = ma_periods
    ma1 = weekly.rolling(p1).mean()
    ma2 = weekly.rolling(p2).mean()
    ma3 = weekly.rolling(p3).mean()
    ma3_slope_pct = (ma3 - ma3.shift(slope_lookback_weeks)) / ma3.shift(slope_lookback_weeks) * 100
    bull_order = (weekly > ma1) & (ma1 > ma2) & (ma2 > ma3)

    tier_a = hist_rising & bull_order & (ma3_slope_pct > FLAT_MA_THRESHOLD_PCT)
    return tier_a.reindex(daily_close.index, method="ffill").fillna(False)

def _daily_screen2_series(df: pd.DataFrame, period: int = ELDER_PERIOD) -> pd.Series:
    """Screen 2 по каждому дню: True, если Bear Power отрицательный, но растёт
    (разворот отката), при Bull Power положительном — та же логика, что в
    indicators.calc_elder_ray, но на всю историю сразу."""
    ema = df["close"].ewm(span=period, adjust=False).mean()
    bull_power = df["high"] - ema
    bear_power = df["low"] - ema
    bear_rising = bear_power > bear_power.shift(1)
    return (bear_power < 0) & bear_rising & (bull_power > 0)

def backtest(coin: str, days: int = 365, tp_pct: float = 0.06, sl_pct: float = 0.03,
             max_hold_days: int = 30, methodology: str = "strict") -> dict:
    """
    Прогоняет правило BUY (Screen 1 Бычий И Screen 2 сработал) по историческим
    данным и считает гипотетическую доходность.

    methodology: "strict" — строгая Elder (MA10/30/60, наклон за 6 недель, как в
    calc_weekly_ma_trend); "fast" — экспериментальная быстрая (MA5/13/20, наклон
    за 4 недели, минимум 24 недели истории, как в calc_weekly_ma_trend_fast).
    Добавлено 2026-09-12 по просьбе Вадима — сравнить методики статистически,
    вместо того чтобы торговать быстрыми сигналами живыми деньгами.
    """
    if methodology == "fast":
        ma_periods, slope_lookback_weeks, min_weeks = (5, 13, 20), 4, 24
    elif methodology == "strict":
        ma_periods, slope_lookback_weeks, min_weeks = (10, 30, 60), 6, 66
    else:
        raise ValueError(f"Неизвестная методика: {methodology!r} (ожидается 'strict' или 'fast')")

    klines = get_klines(coin, "1d", limit=min(days + 500, 1000))
    df = pd.DataFrame(klines)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("open_time")

    closes = df["close"]
    tier_a = _weekly_tier_a_series(closes, ma_periods=ma_periods,
                                    slope_lookback_weeks=slope_lookback_weeks,
                                    min_weeks=min_weeks)
    screen2 = _daily_screen2_series(df)
    entry_signal = tier_a & screen2

    trades = []
    i = 0
    n = len(df)
    while i < n - 1:
        if bool(entry_signal.iloc[i]):
            entry_price = float(closes.iloc[i])
            tp_price = entry_price * (1 + tp_pct)
            sl_price = entry_price * (1 - sl_pct)
            exit_price = None
            exit_reason = "по времени (лимит удержания)"
            end = min(i + max_hold_days, n - 1)
            exit_i = end
            for j in range(i + 1, end + 1):
                low = float(df["low"].iloc[j])
                high = float(df["high"].iloc[j])
                if low <= sl_price:
                    exit_price, exit_reason, exit_i = sl_price, "stop-loss", j
                    break
                if high >= tp_price:
                    exit_price, exit_reason, exit_i = tp_price, "take-profit", j
                    break
            if exit_price is None:
                exit_price = float(closes.iloc[end])

            pnl_pct = (exit_price - entry_price) / entry_price * 100
            trades.append({
                "entry_date": str(closes.index[i].date()),
                "entry_price": round(entry_price, 6),
                "exit_price": round(exit_price, 6),
                "pnl_pct": round(pnl_pct, 2),
                "reason": exit_reason,
            })
            i = exit_i + 1
        else:
            i += 1

    if not trades:
        return {
            "coin": coin.upper(), "trades": 0, "win_rate": None,
            "total_return_pct": 0.0, "avg_return_pct": 0.0, "details": [],
            "tp_pct": tp_pct, "sl_pct": sl_pct, "methodology": methodology,
        }

    wins = [t for t in trades if t["pnl_pct"] > 0]
    total_return = sum(t["pnl_pct"] for t in trades)

    return {
        "coin": coin.upper(),
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_return_pct": round(total_return, 2),
        "avg_return_pct": round(total_return / len(trades), 2),
        "details": trades[-5:],
        "tp_pct": tp_pct,
        "sl_pct": sl_pct,
        "methodology": methodology,
    }

_METHODOLOGY_LABEL = {
    "strict": "Elder's Triple Screen, строгая (MA10/30/60)",
    "fast": "Быстрая эксперимент. (MA5/13/20)",
}

def format_backtest(result: dict) -> str:
    label = _METHODOLOGY_LABEL.get(result.get("methodology", "strict"), "Elder's Triple Screen")
    if result["trades"] == 0:
        return (
            f"📈 Бэктест {result['coin']} ({label}): за выбранный период сигналов Screen 1+2 не было.\n"
            "Попробуй другую монету или измени параметры (tp=/sl=)."
        )

    lines = [
        f"📈 Бэктест {result['coin']} — {label} (TP {result['tp_pct']*100:.0f}%, SL {result['sl_pct']*100:.0f}%)",
        "",
        f"Сделок: {result['trades']}",
        f"Win-rate: {result['win_rate']}%",
        f"Суммарная доходность: {result['total_return_pct']}%",
        f"Средняя доходность на сделку: {result['avg_return_pct']}%",
        "",
        "Последние сделки:",
    ]
    for t in result["details"]:
        lines.append(f"• {t['entry_date']}: {t['entry_price']} → {t['exit_price']} ({t['pnl_pct']}%, {t['reason']})")

    return "\n".join(lines)
