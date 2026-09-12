import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta, time as dt_time
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

from signals import analyze, format_analysis
from scanner import scan_coins, format_scan_result, TOP_COINS, POOLS, all_pool_coins
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
    "/backtest BTC — прогнать стратегию по BTC за ~год (строгая методика)\n"
    "/backtest BTC tp=0.08 sl=0.04 — то же со своими Take-Profit/Stop-Loss\n"
    "/backtest BTC fast — то же, но по быстрой эксперим. методике (MA5/13/20)\n"
    "/watch — показать вотчлист автопроверки\n"
    "/watch XMR VVV — добавить монеты в вотчлист автопроверки\n"
    "/unwatch XMR — убрать монету из вотчлиста\n\n"
    "По умолчанию в вотчлисте твой обычный список из 24 монет (тот же, что в регулярных /scan) — "
    "я проверяю его сам в фоне каждые 15 минут и пишу тебе сразу, как только по любой из монет "
    "появится реальный сигнал BUY — по методике Элдора, Гудмана (пробой) или быстрой (MA5/13/20) — "
    "не нужно самому сидеть и сканировать.\n\n"
    "Плюс раз в 2 часа я сам сканирую вообще все монеты (TOP_COINS + пулы A-E, ~230 тикеров) "
    "и добавляю в отдельный авто-вотчлист те, у которых недельный тренд уже подходящий — их я "
    "тоже проверяю каждые 15 минут вместе с твоим списком. Монеты, у которых тренд перестал "
    "подходить, сами оттуда выпадают на следующем цикле.\n\n"
    "Ордера я не выставляю и в MEXC не захожу — только анализ. Решение и покупку делаешь ты сам."
)

CHECK_INTERVAL_SECONDS = 15 * 60  # автопроверка вотчлиста каждые 15 минут

# Тот же список из 24 монет, что Вадим обычно прогоняет вручную через /scan — используем его
# как вотчлист по умолчанию (2026-09-10, по его просьбе: "как ты ему задал 24 монеты, чтобы
# проверял, которые мы выбрали"), а не узкий список из 1-2 монет.
DEFAULT_WATCHLIST = [
    "HYPE", "XMR", "VVV", "ZEC", "TRX", "RAY", "ETHFI", "VELO", "CAKE", "NEAR",
    "LIT", "PROM", "AERO", "SOL", "LINK", "PYTH", "CRV", "UNI", "XVS", "GMX",
    "JUP", "CVX", "MORPHO", "JST",
]
CHAT_ID_FILE = "chat_id.txt"
WATCHLIST_FILE = "watchlist.txt"

# Широкий скан всех монет (TOP_COINS + пулы A-E, ~230 тикеров) раз в 2 часа — добавлено
# 2026-09-12 по просьбе Вадима: обычный вотчлист из 24 монет не покрывает остальные
# тикеры из /pool a-e, сигнал по ним можно пропустить. Сканировать всё это каждые 15 минут
# нельзя — скан последовательный (блокирующий), 230 монет займут заметное время и будут
# тормозить ответы бота. Поэтому широкий скан — раз в 2 часа отдельным фоновым job'ом,
# он полностью пересобирает auto_watchlist.txt (не дополняет!) по критерию "недельный
# тренд уже подходящий" (signal_type или signal_type_breakout не WAIT) — благодаря
# полной пересборке монеты, у которых тренд перестал быть подходящим, сами выпадают
# на следующем цикле, без отдельной логики удаления.
FULL_SCAN_INTERVAL_SECONDS = 2 * 60 * 60
AUTO_WATCHLIST_FILE = "auto_watchlist.txt"

# coin -> (signal_type Элдора, signal_type_breakout Гудмана, signal_type_fast Быстрой методики)
# на момент последней автопроверки. Нужен, чтобы алертить только на РЕАЛЬНОМ переходе в BUY,
# а не спамить на каждой проверке. Быстрая методика добавлена в кортеж 2026-09-12 — Вадим
# по итогам бэктеста (13 сделок, ~69% win-rate на трёх монетах, сравнимо со строгой методикой)
# решил работать по всем трём методикам, а не только по Элдору и Гудману.
_last_signal_state: dict[str, tuple[str, str, str]] = {}

# Время последнего УСПЕШНОГО прогона _watch_job / _full_scan_job (UTC, в памяти процесса —
# сбрасывается при рестарте бота, это нормально: пустое значение после рестарта — честный
# признак "ещё не проверял", а не ошибка). Нужно для heartbeat-отчёта (см. ниже) — 2026-09-12,
# по просьбе Вадима: "чтобы не получилось, что сигнала ждём, а бот в итоге не работал".
_last_watch_run: datetime | None = None
_last_full_scan_run: datetime | None = None

# Новороссийск — UTC+3 круглый год (без перехода на летнее время).
_NVRSK_TZ = timezone(timedelta(hours=3))

# Три отчёта в день по местному времени Вадима (Новороссийск, UTC+3) — храним как UTC-время,
# т.к. большинство хостингов (Railway и т.п.) крутят процесс в UTC независимо от таймзоны сервера.
HEARTBEAT_TIMES_UTC = [
    dt_time(hour=6, minute=5, tzinfo=timezone.utc),   # 09:05 по Новороссийску
    dt_time(hour=11, minute=5, tzinfo=timezone.utc),  # 14:05 по Новороссийску
    dt_time(hour=17, minute=5, tzinfo=timezone.utc),  # 20:05 по Новороссийску
]


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


def _load_auto_watchlist() -> list[str]:
    """Авто-вотчлист, который сам пересобирает _full_scan_job. В отличие от
    _load_watchlist(), пустой файл/его отсутствие — нормальное состояние
    (например, до первого широкого скана после рестарта), а не повод
    подставлять DEFAULT_WATCHLIST."""
    try:
        with open(AUTO_WATCHLIST_FILE) as f:
            return [c.strip().upper() for c in f.read().split(",") if c.strip()]
    except Exception:
        return []


def _save_auto_watchlist(coins: list[str]):
    try:
        with open(AUTO_WATCHLIST_FILE, "w") as f:
            f.write(",".join(coins))
    except Exception:
        logger.exception("Не смог сохранить авто-вотчлист")


def _combined_watchlist() -> list[str]:
    """Ручной вотчлист (/watch) + авто-вотчлист (широкий скан раз в 2 часа), без дублей."""
    manual = _load_watchlist()
    seen = set(manual)
    combined = list(manual)
    for c in _load_auto_watchlist():
        if c not in seen:
            seen.add(c)
            combined.append(c)
    return combined

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
        # asyncio.to_thread (2026-09-13): analyze() делает несколько блокирующих HTTP-запросов
        # (requests, не aiohttp) — без выноса в отдельный поток такой вызов прямо внутри async
        # хэндлера замораживал бы ВЕСЬ бот (единый event loop) на всё время запроса, включая
        # обработку сообщений от других команд и фоновые job'ы. См. подробный комментарий у
        # scan() ниже — там та же причина обнаружена по жалобе Вадима "бот не отвечает".
        result = await asyncio.to_thread(analyze, coin)
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
        # asyncio.to_thread (2026-09-13, по жалобе Вадима "бот не отвечает"): scan_coins()
        # внутри дергает requests.get (синхронный, блокирующий HTTP) на каждую монету —
        # 3-4 запроса на монету, до 10 сек таймаут на каждый. Вызванный напрямую внутри async
        # хэндлера, он выполняется на ЕДИНСТВЕННОМ event loop бота и блокирует АБСОЛЮТНО ВСЁ
        # (ответы на другие команды, фоновые _watch_job/_full_scan_job) на всё время скана —
        # для 24+ монет это реально могло ощущаться как "бот завис". asyncio.to_thread уводит
        # блокирующий вызов в отдельный поток, event loop остаётся свободным.
        results, errors = await asyncio.to_thread(scan_coins, coins)
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
                results, errors = await asyncio.to_thread(scan_coins, coins)
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
        results, errors = await asyncio.to_thread(scan_coins, coins)
        await _reply_chunked(update, format_scan_result(results, errors, title=f"Пул {key.upper()}"))
    except Exception as e:
        logger.exception("Ошибка сканирования")
        await update.message.reply_text(f"Ошибка при сканировании: {e}")


async def backtest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Использование: /backtest МОНЕТА [tp=0.06] [sl=0.03] [fast]\n"
            "Например: /backtest BTC, /backtest ETH tp=0.08 sl=0.04 или /backtest XMR fast"
        )
        return

    coin = args[0]
    tp_pct, sl_pct = 0.06, 0.03
    methodology = "strict"
    for a in args[1:]:
        if a.startswith("tp="):
            tp_pct = float(a.split("=")[1])
        elif a.startswith("sl="):
            sl_pct = float(a.split("=")[1])
        elif a.lower() in ("fast", "быстрый", "быстрая"):
            methodology = "fast"
        elif a.lower() in ("strict", "строгий", "строгая"):
            methodology = "strict"

    label = "быстрой методике" if methodology == "fast" else "строгой методике"
    await update.message.reply_text(f"Считаю бэктест по {coin.upper()} за ~год ({label}), подожди немного...")
    try:
        result = await asyncio.to_thread(backtest, coin, tp_pct=tp_pct, sl_pct=sl_pct, methodology=methodology)
        await update.message.reply_text(format_backtest(result))
    except Exception as e:
        logger.exception("Ошибка бэктеста")
        await update.message.reply_text(f"Ошибка бэктеста: {e}")

async def watch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    watchlist = _load_watchlist()
    if not args:
        auto = _load_auto_watchlist()
        await update.message.reply_text(
            "Вотчлист сейчас: " + (", ".join(watchlist) if watchlist else "пусто") + "\n"
            f"Авто-вотчлист (широкий скан всех {len(all_pool_coins())} монет раз в "
            f"{FULL_SCAN_INTERVAL_SECONDS // 3600} ч, недельный тренд уже подходящий): "
            + (f"{len(auto)} монет — {', '.join(auto)}" if auto else "пока пусто") + "\n"
            f"Автопроверка обоих списков вместе каждые {CHECK_INTERVAL_SECONDS // 60} мин, алерт только при "
            "реальном переходе в BUY (у любой из трёх методик — Элдор, Гудман или Быстрая).\n\n"
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
    чтобы не спамить одним и тем же сигналом каждые 15 минут.

    Использует ту же scan_coins(), что и /scan и /pool — проверенный пулом путь, а не отдельный
    цикл analyze_raw() по монете: одинаковая нагрузка на MEXC API что при ручном /scan 24, что
    при автопроверке, только теперь она идёт каждые 15 минут сама, без участия Вадима.

    asyncio.to_thread (2026-09-13): с добавлением авто-вотчлиста (2026-09-12) этот job может
    сканировать до ~230 монет за раз (24 ручных + до ~207 авто), а не 24 как раньше. Без выноса
    в отдельный поток синхронный scan_coins() блокировал бы единственный event loop бота на всё
    время скана — весь бот (включая ответы на /scan, /backtest и т.д.) был бы недоступен, пока
    не досканирует все монеты. Обнаружено по жалобе Вадима "бот не отвечает" на /scan 24 монет —
    тот же самый паттерн блокировки, только здесь ещё и раз в 15 минут на бОльшем списке."""
    global _last_watch_run
    chat_id = _load_chat_id()
    if not chat_id:
        return  # ещё ни разу не писал боту после рестарта — некому слать

    results, errors = await asyncio.to_thread(scan_coins, _combined_watchlist())
    if errors:
        logger.warning("Автопроверка вотчлиста: не удалось получить данные по %s", ", ".join(c for c, _ in errors))
    _last_watch_run = datetime.now(timezone.utc)

    for d in results:
        coin = d["coin"]
        cur_elder = d["signal_type"]
        cur_breakout = d["signal_type_breakout"]
        cur_fast = d["signal_type_fast"]
        prev = _last_signal_state.get(coin)
        _last_signal_state[coin] = (cur_elder, cur_breakout, cur_fast)

        if prev is None:
            continue  # первая проверка после рестарта — просто фиксируем базу, без алерта

        prev_elder, prev_breakout, prev_fast = prev
        # Строим текст алерта из того же d, что дал BUY (не повторный запрос analyze(coin) —
        # см. format_analysis() в signals.py: между двумя живыми запросами цена успевала
        # откатиться, и текст алерта мог противоречить его же заголовку).
        #
        # Три независимые проверки (не if/elif), а не один if/elif на все методики —
        # 2026-09-12: раньше Элдор и Гудман были в одной if/elif цепочке, и если обе давали
        # BUY в один и тот же цикл проверки, алерт по Гудману терялся (elif проверялся, только
        # если elder-ветка не сработала). С добавлением третьей методики это стало бы ещё
        # заметнее, поэтому теперь каждая методика алертит независимо — за один цикл может
        # прийти хоть три отдельных сообщения, если все три одновременно дали BUY.
        if cur_elder == "BUY" and prev_elder != "BUY":
            text = format_analysis(d)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 Автосигнал из вотчлиста: {coin} — методика Элдора дала BUY!\n\n{text}",
            )
        if cur_breakout == "BUY" and prev_breakout != "BUY":
            text = format_analysis(d)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 Автосигнал из вотчлиста: {coin} — методика Гудмана (пробой) дала BUY!\n\n{text}",
            )
        if cur_fast == "BUY" and prev_fast != "BUY":
            text = format_analysis(d)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 Автосигнал из вотчлиста: {coin} — быстрая методика (эксперимент, MA5/13/20) дала BUY!\n\n{text}",
            )


async def _full_scan_job(context: ContextTypes.DEFAULT_TYPE):
    """Раз в 2 часа сканирует ВСЕ монеты (TOP_COINS + пулы A-E, без дублей — см.
    all_pool_coins()) и полностью пересобирает auto_watchlist.txt: в него попадают
    монеты, у которых недельный тренд (Screen 1) уже подходящий хотя бы по одной
    из двух методик (signal_type != WAIT или signal_type_breakout != WAIT) — то
    есть кандидаты, которых имеет смысл проверять каждые 15 минут на точку входа.

    Список каждый раз строится заново (не дополняется), поэтому монеты, у которых
    тренд перестал быть подходящим, сами выпадают на следующем цикле — отдельной
    логики удаления не нужно. Сам алерт на BUY по-прежнему шлёт только _watch_job.

    asyncio.to_thread (2026-09-13, см. _watch_job) — этот job сканирует ~207 монет
    за раз, самый долгий синхронный вызов в боте; без выноса в отдельный поток он
    держал бы event loop бота занятым дольше всех остальных job'ов вместе взятых."""
    global _last_full_scan_run
    coins = all_pool_coins()
    results, errors = await asyncio.to_thread(scan_coins, coins)
    if errors:
        logger.warning("Широкий скан (авто-вотчлист): не удалось получить данные по %s",
                        ", ".join(c for c, _ in errors))

    candidates = sorted({
        d["coin"] for d in results
        if d["signal_type"] != "WAIT" or d["signal_type_breakout"] != "WAIT"
    })

    prev_auto = set(_load_auto_watchlist())
    _save_auto_watchlist(candidates)
    _last_full_scan_run = datetime.now(timezone.utc)

    chat_id = _load_chat_id()
    if chat_id:
        added = sorted(set(candidates) - prev_auto)
        removed = sorted(prev_auto - set(candidates))
        if added or removed:
            text = (
                f"🔎 Широкий скан ({len(coins)} монет): в авто-вотчлисте теперь {len(candidates)}.\n"
            )
            if added:
                text += f"Добавлены (тренд стал подходящим): {', '.join(added)}\n"
            if removed:
                text += f"Убраны (тренд больше не подходящий): {', '.join(removed)}"
            await context.bot.send_message(chat_id=chat_id, text=text.strip())


def _format_ago(moment: datetime | None) -> str:
    """'12 мин назад' / 'ни разу с рестарта' — для heartbeat-отчёта."""
    if moment is None:
        return "ни разу с последнего рестарта бота"
    minutes = int((datetime.now(timezone.utc) - moment).total_seconds() // 60)
    if minutes < 1:
        return "меньше минуты назад"
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    return f"{hours} ч {minutes % 60} мин назад"


async def _heartbeat_job(context: ContextTypes.DEFAULT_TYPE):
    """Отчёт 'бот жив' 3 раза в день (09:05 / 14:05 / 20:05 по Новороссийску) — добавлено
    2026-09-12 по просьбе Вадима: "чтобы не получилось, что сигнала ждём, а бот в итоге
    не работал". Не делает новый скан сам — только показывает состояние уже идущих
    фоновых job'ов (_watch_job каждые 15 мин, _full_scan_job раз в 2 часа), поэтому не
    создаёт дополнительной нагрузки на MEXC API."""
    chat_id = _load_chat_id()
    if not chat_id:
        return

    watch_age_min = None if _last_watch_run is None else (datetime.now(timezone.utc) - _last_watch_run).total_seconds() / 60
    stale_warning = ""
    if watch_age_min is not None and watch_age_min > CHECK_INTERVAL_SECONDS / 60 + 10:
        stale_warning = (
            f"\n⚠️ Последняя проверка вотчлиста была {_format_ago(_last_watch_run)} — "
            f"дольше обычного (норма — каждые {CHECK_INTERVAL_SECONDS // 60} мин). "
            "Возможно, бот перезапустился или завис — стоит проверить."
        )

    # Кортеж состояния теперь тройной (Элдор, Гудман, Быстрая) — 2026-09-12, см. _watch_job.
    buy_count = sum(1 for state in _last_signal_state.values() if "BUY" in state)
    watch_count = sum(1 for state in _last_signal_state.values() if "WATCH" in state)

    now_local = datetime.now(timezone.utc).astimezone(_NVRSK_TZ)
    text = (
        f"✅ Бот на связи, {now_local.strftime('%d.%m %H:%M')} (Новороссийск).\n"
        f"Ручной вотчлист: {len(_load_watchlist())} монет. "
        f"Авто-вотчлист: {len(_load_auto_watchlist())} монет.\n"
        f"Последняя проверка вотчлиста (15 мин): {_format_ago(_last_watch_run)}.\n"
        f"Последний широкий скан (2 ч): {_format_ago(_last_full_scan_run)}.\n"
        f"Сейчас отслеживается {len(_last_signal_state)} монет, из них BUY: {buy_count}, WATCH: {watch_count}."
        f"{stale_warning}"
    )
    await context.bot.send_message(chat_id=chat_id, text=text)


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
    app.job_queue.run_repeating(_full_scan_job, interval=FULL_SCAN_INTERVAL_SECONDS, first=300)
    for t in HEARTBEAT_TIMES_UTC:
        app.job_queue.run_daily(_heartbeat_job, time=t)

    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
