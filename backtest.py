"""
Бэктест торговой стратегии по историческим дневным данным (~1 год).
Правило теперь совпадает с живой логикой signals.py (Elder's Triple Screen):
покупка при недельном Screen 1 = Бычий (MACD-гистограмма растёт, MA10>MA30>MA60,
MA60 растёт >1% за 6 недель) И дневном Screen 2 = Bear Power отрицательный,
но разворачивается вверх при Bull Power > 0. RSI в правило входа не входит —
он вторичное подтверждение, как и в живом сканере.
Выход по Take-Profit, Stop-Loss или по лимиту времени удержания.
Недельная структура для истории приближённо считается ресемплингом дневных
закрытий в 7-дневные свечи (MEXC отдаёт недельные свечи только текущим
окном, а не произвольным диапазоном в прошлом).
"""
import pandas as pd
from mexc_api import get_klines

FLAT_MA_THRESHOLD_PCT = 1.0
ELDER_PERIOD = 13


def _weekly_tier_a_series(daily_close: pd.Series) -> pd.Series:
    """Screen 1 по каждому дню истории: True, если на тот момент недельная
    структура была Бычий (Tier A) — та же логика, что в signals.calc_weekly_ma_trend,
    но векторизованная и с недельным MACD-гистограммой в довесок."""
    weekly = daily_close.resample("7D").last().dropna()
    if len(weekly) < 66:
        raise ValueError("Недостаточно исторических данных для недельного Screen 1 (нужно ~66 недель)")

    ema_fast = weekly.ewm(span=12, adjust=False).mean()
    ema_slow = weekly.ewm(span=26, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line
    hist_rising = hist > hist.shift(1)

    ma10 = weekly.rolling(10).mean()
    ma30 = weekly.rolling(30).mean()
    ma60 = weekly.rolling(60).mean()
    ma60_slope_pct = (ma60 - ma60.shift(6)) / ma60.shift(6) * 100
    bull_order = (weekly > ma10) & (ma10 > ma30) & (ma30 > ma60)

    tier_a = hist_rising & bull_order & (ma60_slope_pct > FLAT_MA_THRESHOLD_PCT)
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
             max_hold_days: int = 30) -> dict:
    """
    Прогоняет правило BUY (Screen 1 Бычий И Screen 2 сработал) по историческим
    данным и считает гипотетическую доходность.
    """
    klines = get_klines(coin, "1d", limit=min(days + 90, 1000))
    df = pd.DataFrame(klines)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("open_time")

    closes = df["close"]
    tier_a = _weekly_tier_a_series(closes)
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
            "tp_pct": tp_pct, "sl_pct": sl_pct,
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
    }


def format_backtest(result: dict) -> str:
    if result["trades"] == 0:
        return (
            f"📈 Бэктест {result['coin']}: за выбранный период сигналов Screen 1+2 не было.\n"
            "Попробуй другую монету или измени параметры (tp=/sl=)."
        )

    lines = [
        f"📈 Бэктест {result['coin']} — Elder's Triple Screen (TP {result['tp_pct']*100:.0f}%, SL {result['sl_pct']*100:.0f}%)",
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
