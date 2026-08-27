"""
Бэктест торговой стратегии по историческим дневным данным (~1 год).
Правило совпадает с signals.py: покупка при недельном MACD-тренде вверх
и дневном RSI ниже порога; выход по Take-Profit, Stop-Loss или по лимиту
времени удержания.
Недельный тренд для истории приближённо считается ресемплингом дневных
закрытий в 7-дневные свечи (MEXC отдаёт недельные свечи только текущим
окном, а не произвольным диапазоном в прошлом).
"""
import pandas as pd
from mexc_api import get_klines


def _rsi_series(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _weekly_trend_series(daily_close: pd.Series) -> pd.Series:
    weekly = daily_close.resample("7D").last().dropna()
    if len(weekly) < 35:
        raise ValueError("Недостаточно исторических данных для недельного MACD")
    ema_fast = weekly.ewm(span=12, adjust=False).mean()
    ema_slow = weekly.ewm(span=26, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    trend_up = (macd_line - signal_line) > 0
    return trend_up.reindex(daily_close.index, method="ffill")


def backtest(coin: str, days: int = 365, rsi_threshold: float = 50,
             tp_pct: float = 0.06, sl_pct: float = 0.03, max_hold_days: int = 30) -> dict:
    """
    Прогоняет правило BUY (недельный тренд вверх + дневной RSI < rsi_threshold)
    по историческим данным и считает гипотетическую доходность.
    """
    klines = get_klines(coin, "1d", limit=min(days + 60, 1000))
    df = pd.DataFrame(klines)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("open_time")

    closes = df["close"]
    rsi_series = _rsi_series(closes)
    weekly_up = _weekly_trend_series(closes)

    trades = []
    i = 0
    n = len(df)
    while i < n - 1:
        if bool(weekly_up.iloc[i]) and rsi_series.iloc[i] < rsi_threshold:
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
            f"📈 Бэктест {result['coin']}: за выбранный период сигналов на покупку не было.\n"
            "Попробуй другую монету или измени параметры (tp=/sl=)."
        )

    lines = [
        f"📈 Бэктест {result['coin']} (TP {result['tp_pct']*100:.0f}%, SL {result['sl_pct']*100:.0f}%)",
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
