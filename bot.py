import logging
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

from signals import analyze
from scanner import scan_coins, format_scan_result, TOP_COINS
from backtest import backtest, format_backtest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

WELCOME = (
    "Привет, Вадим! Я твой торговый аналитик по MEXC.\n\n"
    "Просто напиши мне тикер монеты, например:\n"
    "ADA\n"
    "SOL\n"
    "BTC\n\n"
    "Я подтяну графики с MEXC (неделя + день), посчитаю RSI, MACD, объём, "
    "гляну индекс страха/жадности и выдам тебе анализ с готовым сигналом.\n\n"
    "Другие команды:\n"
    "/scan — просканировать топ-10 монет и отсортировать по силе сигнала\n"
    "/scan 20 — просканировать топ-20\n"
    "/scan BTC ETH SOL — просканировать свой список монет (до 50 штук)\n"
    "/backtest BTC — прогнать стратегию по BTC за ~год\n"
    "/backtest BTC tp=0.08 sl=0.04 — то же со своими Take-Profit/Stop-Loss"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME)


async def handle_coin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coin = update.message.text.strip()
    if not coin:
        return
    await update.message.chat.send_action("typing")
    try:
        result = analyze(coin)
        await update.message.reply_text(result)
    except Exception as e:
        logger.exception("Ошибка анализа")
        await update.message.reply_text(
            f"Не получилось найти монету '{coin}' или произошла ошибка.\n"
            f"Проверь тикер (например ADA, BTC, SOL) и попробуй снова.\n\n"
            f"Детали: {e}"
        )


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args and args[0].isdigit():
        n = min(int(args[0]), 50)
        coins = TOP_COINS[:n]
    elif args:
        coins = [a.strip() for a in args][:50]
    else:
        coins = TOP_COINS[:10]

    await update.message.chat.send_action("typing")
    await update.message.reply_text(f"Сканирую {len(coins)} монет, подожди немного...")
    try:
        results, errors = scan_coins(coins)
        await update.message.reply_text(format_scan_result(results, errors))
    except Exception as e:
        logger.exception("Ошибка сканирования")
        await update.message.reply_text(f"Ошибка при сканировании: {e}")


async def backtest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Использование: /backtest МОНЕТА [tp=0.06] [sl=0.03]\n"
            "Например: /backtest BTC или /backtest ETH tp=0.08 sl=0.04"
        )
        return

    coin = args[0]
    tp_pct, sl_pct = 0.06, 0.03
    for a in args[1:]:
        if a.startswith("tp="):
            tp_pct = float(a.split("=")[1])
        elif a.startswith("sl="):
            sl_pct = float(a.split("=")[1])

    await update.message.reply_text(f"Считаю бэктест по {coin.upper()} за ~год, подожди немного...")
    try:
        result = backtest(coin, tp_pct=tp_pct, sl_pct=sl_pct)
        await update.message.reply_text(format_backtest(result))
    except Exception as e:
        logger.exception("Ошибка бэктеста")
        await update.message.reply_text(f"Ошибка бэктеста: {e}")


def main():
    if not TOKEN:
        raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN в переменных окружения")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("backtest", backtest_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_coin))

    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
