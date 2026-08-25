import logging
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

from signals import analyze

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
    "гляну индекс страха/жадности и выдам тебе анализ с готовым сигналом."
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


def main():
    if not TOKEN:
        raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN в переменных окружения")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_coin))

    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
