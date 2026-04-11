"""Command handlers and bot runtime logic."""
import html
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import Config
from database import add_chat, db, get_metadata, get_schedule_for_day, get_schedule_for_week, init_db, set_metadata
from middleware.access_middleware import AccessMiddleware
from services.schedule_service import ScheduleService
from services.schedule_updater import ScheduleUpdater

config = Config.from_env()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHECK_INTERVAL_HOURS = 1
DAILY_MAILING_HOUR = 19
DAILY_MAILING_MINUTE = 0
WEEKLY_PREVIEW_HOUR = 13
WEEKLY_PREVIEW_MINUTE = 0
MANUAL_UPDATE_THROTTLE_SECONDS = 120
MESSAGE_SEPARATOR = "➖➖➖➖➖➖➖➖➖➖"
CURRENT_SCHEDULE_PDF = Path(__file__).resolve().parent / "downloads" / "raspisanie_po_gruppam.pdf"
PRE_MONDAY_WEEKLY_SENT_KEY = "last_pre_monday_weekly_sent_period"

MOSCOW_TZ = ZoneInfo("Europe/Moscow")

bot_session = AiohttpSession(proxy=config.telegram_proxy_url) if config.telegram_proxy_url else None
bot = Bot(
    token=config.token,
    session=bot_session,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)
schedule_service = ScheduleService(bot, config, db)
dp.message.middleware(AccessMiddleware(db=db, admin_ids=config.admin_ids))

WEEK_DAYS_MAP = {
    0: "ПОНЕДЕЛЬНИК",
    1: "ВТОРНИК",
    2: "СРЕДА",
    3: "ЧЕТВЕРГ",
    4: "ПЯТНИЦА",
    5: "СУББОТА",
    6: "ВОСКРЕСЕНЬЕ",
}


def now_moscow() -> datetime:
    return datetime.now(MOSCOW_TZ)


async def notify_admins(text: str, throttle_key: str, interval_minutes: int = 60) -> None:
    await schedule_service.notify_admins(text, throttle_key, interval_minutes)


def is_admin_message(message: types.Message) -> bool:
    return bool(message.from_user and message.from_user.id in config.admin_ids)


def get_date_from_period(period_str: str, day_name: str) -> str:
    """Resolve a day name to a concrete date inside the stored week period."""
    try:
        start_date_str = period_str.split("-", 1)[0].strip()
        start_date = datetime.strptime(start_date_str, "%d.%m.%Y")

        for idx, name in WEEK_DAYS_MAP.items():
            if name == day_name.upper():
                delta = (idx - start_date.weekday()) % 7
                return (start_date + timedelta(days=delta)).strftime("%d.%m.%Y")
    except Exception as exc:
        logger.error("Date resolution error: %s", exc)
    return "?.?.????"


def escape_html(value: object) -> str:
    return html.escape(str(value), quote=False)


def get_next_study_date(base_date: date) -> date:
    target_date = base_date + timedelta(days=1)
    while target_date.weekday() == 6:
        target_date += timedelta(days=1)
    return target_date


def should_send_weekly_preview(target_date: date) -> bool:
    return target_date.weekday() == 0


def should_skip_daily_evening_mailing(base_date: date) -> bool:
    return base_date.weekday() == 5


def format_schedule_message(day_name: str, period: str, lessons: list[tuple]) -> str:
    """Format one day schedule as HTML-safe Telegram text."""
    safe_day_name = escape_html(day_name)
    date_str = get_date_from_period(period, day_name) if period else ""
    safe_date = escape_html(date_str) if date_str else ""

    text = f"📅 <b>{safe_day_name}</b>"
    if safe_date:
        text += f" <i>({safe_date})</i>"
    text += f"\n{MESSAGE_SEPARATOR}\n"

    if not lessons:
        return text + "🎉 <b>Пар нет!</b> <i>Можно отдыхать.</i>\n"

    for lesson in lessons:
        num, start, end, subject, room = lesson
        num_emoji = {
            1: "1️⃣",
            2: "2️⃣",
            3: "3️⃣",
            4: "4️⃣",
            5: "5️⃣",
            6: "6️⃣",
        }.get(num, f"{num}.")

        safe_time = escape_html(f"{start} - {end}")
        safe_subject = escape_html(subject)
        safe_room = html.escape(room, quote=True)

        text += f"{num_emoji} <b>{safe_time}</b>"
        if room:
            text += f" 🚪 <code>{safe_room}</code>"
        text += f"\n📚 {safe_subject}\n\n"

    return text


def format_week_schedule(period: str, schedule_data: list[dict]) -> str:
    """Format a whole week schedule as HTML-safe Telegram text."""
    safe_period = escape_html(period)
    text = "🆕 <b>РАСПИСАНИЕ НА НЕДЕЛЮ</b>\n"
    text += f"🗓 <b>Период:</b> {safe_period}\n"
    text += "\n"

    for day in schedule_data:
        day_name = day["day"]
        lessons = day["lessons"]
        if not lessons:
            continue

        safe_day_name = escape_html(day_name)
        safe_date = escape_html(get_date_from_period(period, day_name))
        text += f"🔹 <b>{safe_day_name}</b> <i>({safe_date})</i>\n"

        for lesson in lessons:
            subject = escape_html(lesson["subject"])
            line = f"   {lesson['num']}. {subject}"
            if lesson["room"]:
                line += f" (<code>{escape_html(lesson['room'])}</code>)"
            text += line + "\n"

        text += "\n"

    text += "<i>Бот автоматически будет присылать расписание на следующий учебный день в 19:00.</i>"
    return text


def build_schedule_pdf_caption(period: str) -> str:
    return f"📎 PDF расписания за период: {escape_html(period)}"


schedule_updater = ScheduleUpdater(
    config,
    db,
    schedule_service,
    format_week_schedule=format_week_schedule,
    format_document_caption=build_schedule_pdf_caption,
)


async def get_schedule_for_target_date(target_date: date) -> tuple[str, Optional[str], list]:
    day_name = WEEK_DAYS_MAP[target_date.weekday()]
    period, lessons = await get_schedule_for_day(
        config.group_name,
        day_name,
        target_date=target_date,
    )
    return day_name, period, lessons


async def get_schedule_for_period(period: str) -> list[dict]:
    return await get_schedule_for_week(config.group_name, period)


async def check_manual_update_throttle() -> int:
    last_manual_update = await get_metadata("last_manual_update_at")
    if not last_manual_update:
        return 0

    try:
        last_dt = datetime.fromisoformat(last_manual_update)
    except ValueError:
        return 0

    elapsed = (now_moscow() - last_dt).total_seconds()
    remaining = MANUAL_UPDATE_THROTTLE_SECONDS - int(elapsed)
    return max(remaining, 0)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    chat_title = message.chat.title or (message.from_user and message.from_user.full_name) or "Чат"
    thread_id = message.message_thread_id

    await add_chat(message.chat.id, chat_title, message.chat.type, thread_id)

    await message.answer(
        (
            f"👋 Привет! Я бот группы <b>{escape_html(config.group_name)}</b>.\n"
            "Я запомнил этот канал связи.\n\n"
            "Теперь я буду работать автоматически:\n"
            "🔹 При появлении нового расписания пришлю его целиком.\n"
            "🔹 Каждый вечер в 19:00 пришлю расписание на следующий учебный день."
        ),
        reply_markup=types.ReplyKeyboardRemove(),
    )


@dp.message(Command("today"))
async def cmd_today(message: types.Message):
    today = now_moscow().date()
    day_name, period, lessons = await get_schedule_for_target_date(today)

    if day_name == "ВОСКРЕСЕНЬЕ":
        await message.answer("Сегодня воскресенье, пар нет.")
        return

    if period:
        await message.answer(format_schedule_message(day_name, period, lessons))
    else:
        await message.answer("🤷‍♂️ Расписание не найдено.")


@dp.message(Command("tomorrow"))
async def cmd_tomorrow(message: types.Message):
    target_date = get_next_study_date(now_moscow().date())
    day_name, period, lessons = await get_schedule_for_target_date(target_date)

    if period:
        await message.answer(format_schedule_message(day_name, period, lessons))
    else:
        await message.answer("🤷‍♂️ Расписание на следующий учебный день не найдено.")


@dp.message(Command("update"))
async def cmd_update(message: types.Message):
    if not is_admin_message(message):
        await message.answer("🚫 Команда /update доступна только администраторам.")
        return

    remaining = await check_manual_update_throttle()
    if remaining:
        await message.answer(
            f"⏳ Обновление уже запускали недавно. Попробуй снова через {remaining} сек."
        )
        return

    await set_metadata("last_manual_update_at", now_moscow().isoformat())
    status_msg = await message.answer("⏳ Проверяю сайт...")

    try:
        period, schedule, _ = await check_and_update_schedule(
            notify_users=False,
            reason="manual",
        )
        if period and schedule:
            await status_msg.edit_text("✅ <b>Обновлено!</b>")
            await message.answer(format_week_schedule(period, schedule))
        else:
            await status_msg.edit_text("🛑 Нового расписания нет.")
    except Exception as exc:
        logger.exception("Manual update error: %s", exc)
        await status_msg.edit_text("❌ Ошибка.")


@dp.message(Command("reparse"))
async def cmd_reparse(message: types.Message):
    if not is_admin_message(message):
        await message.answer("🚫 Команда /reparse доступна только администраторам.")
        return

    remaining = await check_manual_update_throttle()
    if remaining:
        await message.answer(
            f"⏳ Перепарс уже запускали недавно. Попробуй снова через {remaining} сек."
        )
        return

    await set_metadata("last_manual_update_at", now_moscow().isoformat())
    status_msg = await message.answer("⏳ Принудительно перепарсиваю текущий файл...")

    try:
        period, schedule, _ = await check_and_update_schedule(
            notify_users=False,
            reason="manual_reparse",
            force=True,
        )
        if period and schedule:
            await status_msg.edit_text("✅ <b>Перепарс выполнен.</b>")
            await message.answer(format_week_schedule(period, schedule))
        else:
            await status_msg.edit_text("❌ Не удалось перепарсить файл.")
    except Exception as exc:
        logger.exception("Manual reparse error: %s", exc)
        await status_msg.edit_text("❌ Ошибка перепарса.")


async def broadcast_message(text: str, document_path: str | None = None) -> int:
    return await schedule_service.broadcast_message(text, document_path)


async def check_and_update_schedule(
    notify_users: bool = True,
    reason: str = "scheduled",
    force: bool = False,
) -> tuple[Optional[str], Optional[list], bool]:
    return await schedule_updater.check_and_update(
        notify_users=notify_users,
        reason=reason,
        force=force,
    )


async def daily_evening_mailing() -> None:
    base_date = now_moscow().date()
    if should_skip_daily_evening_mailing(base_date):
        return

    target_date = get_next_study_date(base_date)
    last_sent = await get_metadata("last_daily_sent_date")
    if last_sent == target_date.isoformat():
        return

    day_name, period, lessons = await get_schedule_for_target_date(target_date)
    if period:
        message_text = "🌙 <b>Расписание на завтра:</b>\n\n"
        message_text += format_schedule_message(day_name, period, lessons)
        await broadcast_message(message_text)
        await set_metadata("last_daily_sent_date", target_date.isoformat())
    else:
        await notify_admins(
            "⚠️ Не найдено расписание для вечерней рассылки.",
            "daily_missing",
            180,
        )


async def weekly_preview_mailing(target_date: date | None = None) -> None:
    target_date = target_date or get_next_study_date(now_moscow().date())
    day_name, period, monday_lessons = await get_schedule_for_target_date(target_date)
    if not period or not monday_lessons:
        await notify_admins(
            "⚠️ Не удалось собрать воскресную недельную рассылку.",
            "weekly_preview_missing",
            180,
        )
        return

    weekly_schedule = await get_schedule_for_period(period)
    if not any(day["lessons"] for day in weekly_schedule):
        await notify_admins(
            "⚠️ В БД нет недельного расписания для воскресной рассылки.",
            "weekly_preview_empty",
            180,
        )
        return

    last_sent = await get_metadata(PRE_MONDAY_WEEKLY_SENT_KEY)
    if last_sent == period:
        return

    message_text = "🗓 <b>РАСПИСАНИЕ НА НОВУЮ НЕДЕЛЮ</b>\n\n"
    message_text += format_week_schedule(period, weekly_schedule)
    document_path = str(CURRENT_SCHEDULE_PDF) if CURRENT_SCHEDULE_PDF.exists() else None
    await schedule_service.broadcast_message(
        message_text,
        document_path=document_path,
        document_caption=build_schedule_pdf_caption(period),
        pin_document=bool(document_path),
    )
    await set_metadata(PRE_MONDAY_WEEKLY_SENT_KEY, period)


async def safe_check_schedule() -> None:
    try:
        await check_and_update_schedule(notify_users=True, reason="scheduled")
    except Exception as exc:
        logger.exception("Scheduled check error: %s", exc)
        await notify_admins(
            "❌ Ошибка плановой проверки расписания.",
            "scheduled_error",
            60,
        )


async def safe_daily_mailing() -> None:
    try:
        await daily_evening_mailing()
    except Exception as exc:
        logger.exception("Daily mailing error: %s", exc)
        await notify_admins(
            "❌ Ошибка вечерней рассылки.",
            "daily_error",
            60,
        )


async def safe_weekly_preview_mailing() -> None:
    try:
        await weekly_preview_mailing()
    except Exception as exc:
        logger.exception("Weekly preview mailing error: %s", exc)
        await notify_admins(
            "❌ Ошибка недельной рассылки перед понедельником.",
            "weekly_preview_error",
            60,
        )


async def on_startup(_bot: Bot | None = None) -> None:
    await startup_recovery()


async def startup_recovery() -> None:
    try:
        await check_and_update_schedule(notify_users=True, reason="startup")
    except Exception as exc:
        logger.exception("Startup recovery error: %s", exc)
        await notify_admins(
            "❌ Ошибка восстановления при старте.",
            "startup_error",
            60,
        )
        return

    now = now_moscow()

    if now.weekday() == 6 and now.hour >= WEEKLY_PREVIEW_HOUR:
        await weekly_preview_mailing()

    if now.hour < DAILY_MAILING_HOUR:
        return

    target_date = get_next_study_date(now.date())
    last_sent = await get_metadata("last_daily_sent_date")
    if last_sent != target_date.isoformat():
        await daily_evening_mailing()


async def on_shutdown(_bot: Bot | None = None) -> None:
    scheduler.shutdown(wait=True)
    await db.close()
    logger.info("Bot stopped cleanly")


async def create_app() -> tuple[Bot, Dispatcher]:
    await init_db()
    return bot, dp


async def main() -> None:
    bot_instance, dispatcher = await create_app()
    dispatcher.startup.register(on_startup)
    dispatcher.shutdown.register(on_shutdown)

    scheduler.add_job(safe_check_schedule, "interval", hours=CHECK_INTERVAL_HOURS)
    scheduler.add_job(
        safe_daily_mailing,
        "cron",
        hour=DAILY_MAILING_HOUR,
        minute=DAILY_MAILING_MINUTE,
    )
    scheduler.add_job(
        safe_weekly_preview_mailing,
        "cron",
        day_of_week="sun",
        hour=WEEKLY_PREVIEW_HOUR,
        minute=WEEKLY_PREVIEW_MINUTE,
    )

    scheduler.start()
    await bot_instance.delete_webhook(drop_pending_updates=True)
    await dispatcher.start_polling(bot_instance)
