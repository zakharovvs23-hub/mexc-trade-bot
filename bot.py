import json
import logging
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

from signals import analyze, analyze_raw
from scanner import scan_coins, format_scan_result, TOP_COINS, POOLS
from backtest import backtest, format_backtest

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

TELEGRAM_MSG_LIMIT = 3500  # с запасом от жёсткого лимита Telegram в 4096 символов

WELCOME = (
    "Привет, Вадим! Я твой торговый аналитик по MEXC.\n\n"
    "Просто напиши мне тикер монеты, например:\n"
    "ADA\n"
    "SOL\n"
    "BTC\n\n"
    "Я подтяну графики с MEXC (неделя + день), прогоню полный Elder's Triple Screen "
    "(недельный тренд + дневной Bull/Bear Power), гляну RSI, объём, индекс страха/жадности "
    "и выдам сигнал: BUY (точка входа готова), WATCH (тренд бычий, входа ещё нет) или WAIT.\n\n"
    "Команды:\n"
    "/scan — просканировать топ-10 монет пулом и отсортировать по силе сигнала\n"
    "/scan 20 — просканировать топ-20\n"
    "/scan BTC ETH SOL — просканировать свой список монет пулом (до 50 штук за раз)\n"
    "/pool a (b, c, d, e) — просканировать пулом весь фиксированный пул ротации, "
    "без необходимости присылать список тикеров вручную\n"
    "/pool all — просканировать все пулы A-E подряд (199 тикеров, займёт время)\n"
    "/backtest BTC — прогнать стратегию по BTC за ~год\n"
    "/backtest BTC tp=0.08 sl=0.04 — то же со своими Take-Profit/Stop-Loss\n"
    "/watch — показать вотчлист автопроверки\n"
    "/watch XMR VVV — добавить монеты в вотчлист автопроверки\n"
    "/unwatch XMR — убрать монету из вотчлиста\n\n"
    "Монеты из вотчлиста я проверяю сам в фоне каждые 15 минут и пишу тебе сразу, "
    "как только по одной из них появится реальный сигнал BUY — не нужно самому сидеть и сканировать.\n\n"
    "Ордера я не выставляю и в MEXC не захожу — только анализ. Решение и покупку делаешь ты сам."
)

CHECK_INTERVAL_SECONDS = 15 * 60  # автопроверка вотчлиста каждые 15 минут
DEFAULT_WATCHLIST = ["XMR", "VVV"]
CHAT_ID_FILE = "chat_id.txt"
WATCHLIST_FILE = "watchlist.txt"

# coin -> (signal_type Элдора, signal_type_breakout Гудмана) на момент последней автопроверки.
# Нужен, чтобы алертить только на РЕАЛЬНОМ переходе в BUY, а не спамить на каждой проверке.
_last_signal_state: dict[str, tuple[str, str]] = {}


def _load_chat_id():
    try:
        with open(CHAT_ID_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _save_chat_id(chat_id: int):
    try:
        with open(CHAT_ID_FILE, "w") as f:
            f.write(str(chat_id))
    except Exception:
        logger.exception("Не смог сохранить chat_id")


def _load_watchlist() -> list[str]:
    try:
        with open(WATCHLIST_FILE) as f:
            coins = [c.strip().upper() for c in f.read().split(",") if c.strip()]
            return coins if coins else list(DEFAULT_WATCHLIST)
    except Exception:
        return list(DEFAULT_WATCHLIST)


def _save_watchlist(coins: list[str]):
    try:
        with open(WATCHLIST_FILE, "w") as f:
            f.write(",".join(coins))
    except Exception:
        logger.exception("Не смог сохранить вотчлист")


def _chunk_text(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """Режет длинный текст на части по границам строк, не разрывая строку пополам,
    чтобы не упереться в лимит Telegram (4096 символов на сообщение)."""
    lines = text.split("\n")
    chunks = []
    current = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > limit and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks or [text]


async def _reply_chunked(update: Update, text: str):
    for chunk in _chunk_text(text):
        await update.message.reply_text(chunk)


async def _capture_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запоминает chat_id при любом сообщении от Вадима — нужен фоновой автопроверке,
    чтобы знать, кому слать алерт, даже сразу после рестарта бота (до первого /start)."""
    if update.effective_chat and _load_chat_id() != update.effective_chat.id:
        _save_chat_id(update.effective_chat.id)


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
    await update.message.reply_text(f"Сканирую {len(coins)} монет пулом, подожди немного...")
    try:
        results, errors = scan_coins(coins)
        await _reply_chunked(update, format_scan_result(results, errors))
    except Exception as e:
        logger.exception("Ошибка сканирования")
        await update.message.reply_text(f"Ошибка при сканировании: {e}")


async def pool_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Использование: /pool a (или b, c, d, e, all)\n"
            f"Доступные пулы: {', '.join(k.upper() for k in POOLS)} "
            f"({sum(len(v) for v in POOLS.values())} тикеров всего)"
        )
        return

    key = args[0].strip().lower()

    if key == "all":
        for pool_key, coins in POOLS.items():
            await update.message.chat.send_action("typing")
            await update.message.reply_text(f"Сканирую пул {pool_key.upper()} ({len(coins)} монет)...")
            try:
                results, errors = scan_coins(coins)
                await _reply_chunked(update, format_scan_result(results, errors, title=f"Пул {pool_key.upper()}"))
            except Exception as e:
                logger.exception("Ошибка сканирования пула %s", pool_key)
                await update.message.reply_text(f"Ошибка при сканировании пула {pool_key.upper()}: {e}")
        return

    if key not in POOLS:
        await update.message.reply_text(
            f"Не знаю пул '{key}'. Доступные: {', '.join(k.upper() for k in POOLS)}, или 'all'."
        )
        return

    coins = POOLS[key]
    await update.message.chat.send_action("typing")
    await update.message.reply_text(f"Сканирую пул {key.upper()} ({len(coins)} монет)...")
    try:
        results, errors = scan_coins(coins)
        await _reply_chunked(update, format_scan_result(results, errors, title=f"Пул {key.upper()}"))
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


async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    watchlist = _load_watchlist()
    if not args:
        await update.message.reply_text(
            "Вотчлист сейчас: " + (", ".join(watchlist) if watchlist else "пусто") + "\n"
            f"Автопроверка каждые {CHECK_INTERVAL_SECONDS // 60} мин, алерт только при реальном "
            "переходе в BUY (у любой из двух методик — Элдор или Гудман).\n\n"
            "Использование: /watch МОНЕТА [МОНЕТА2 ...] — добавить в вотчлист"
        )
        return
    added = []
    for a in args:
        c = a.strip().upper()
        if c and c not in watchlist:
            watchlist.append(c)
            added.append(c)
    _save_watchlist(watchlist)
    text = f"В вотчлисте теперь: {', '.join(watchlist)}"
    if added:
        text += f"\nДобавил: {', '.join(added)}"
    await update.message.reply_text(text)


async def unwatch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    watchlist = _load_watchlist()
    if not args:
        await update.message.reply_text("Укажи монету(ы) для удаления из вотчлиста, например: /unwatch XMR")
        return
    removed = []
    for a in args:
        c = a.strip().upper()
        if c in watchlist:
            watchlist.remove(c)
            removed.append(c)
    _save_watchlist(watchlist)
    text = ""
    if removed:
        text += f"Убрал: {', '.join(removed)}\n"
    text += f"В вотчлисте теперь: {', '.join(watchlist) if watchlist else 'пусто'}"
    await update.message.reply_text(text)

async def _watch_job(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая автопроверка вотчлиста. Алертит только на переходе в BUY (не на каждом тике),
    чтобы не спамить одним и тем же сигналом каждые 15 минут."""
    chat_id = _load_chat_id()
    if not chat_id:
        return  # ещё ни разу не писал боту после рестарта — некому слать

    for coin in _load_watchlist():
        try:
            d = analyze_raw(coin)
        except Exception:
            logger.exception("Ошибка автопроверки вотчлиста по %s", coin)
            continue

        cur_elder = d["signal_type"]
        cur_breakout = d["signal_type_breakout"]
        prev = _last_signal_state.get(coin)
        _last_signal_state[coin] = (cur_elder, cur_breakout)

        if prev is None:
            continue  # первая проверка после рестарта — просто фиксируем базу, без алерта

        prev_elder, prev_breakout = prev
        if cur_elder == "BUY" and prev_elder != "BUY":
            text = analyze(coin)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 Автосигнал из вотчлиста: {coin} — методика Элдора дала BUY!\n\n{text}",
            )
        elif cur_breakout == "BUY" and prev_breakout != "BUY":
            text = analyze(coin)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 Автосигнал из вотчлиста: {coin} — методика Гудмана (пробой) дала BUY!\n\n{text}",
            )


def main():
    if not TOKEN:
        raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN в переменных окружения")

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("scan", scan))
    app.add_handler(CommandHandler("pool", pool_cmd))
    app.add_handler(CommandHandler("backtest", backtest_cmd))
    app.add_handler(CommandHandler("watch", watch_cmd))
    app.add_handler(CommandHandler("unwatch", unwatch_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_coin))
    app.add_handler(MessageHandler(filters.ALL, _capture_chat_id), group=1)

    app.job_queue.run_repeating(_watch_job, interval=CHECK_INTERVAL_SECONDS, first=60)

    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
