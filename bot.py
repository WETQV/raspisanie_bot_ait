"""Хендлеры и логика бота."""
import logging
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from config import Config
from database import (
    db,
    init_db,
    save_schedule,
    add_chat,
    get_chats,
    get_schedule_for_day,
    is_chat_registered,
    get_metadata,
    set_metadata,
)
from services.schedule_service import ScheduleService
from services.schedule_updater import ScheduleUpdater
from middleware.access_middleware import AccessMiddleware
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- КОНФИГУРАЦИЯ ---
config = Config.from_env()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Константы расписания
CHECK_INTERVAL_HOURS = 1
DAILY_MAILING_HOUR = 19
DAILY_MAILING_MINUTE = 0

# Часовой пояс (МСК)
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

# Включаем HTML разметку по умолчанию
bot = Bot(token=config.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)
schedule_service = ScheduleService(bot, config, db)
dp.message.middleware(AccessMiddleware(db=db, admin_ids=config.admin_ids))

WEEK_DAYS_MAP = {
    0: 'ПОНЕДЕЛЬНИК',
    1: 'ВТОРНИК',
    2: 'СРЕДА',
    3: 'ЧЕТВЕРГ',
    4: 'ПЯТНИЦА',
    5: 'СУББОТА',
    6: 'ВОСКРЕСЕНЬЕ'
}

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def now_moscow() -> datetime:
    return datetime.now(MOSCOW_TZ)

async def notify_admins(text: str, throttle_key: str, interval_minutes: int = 60) -> None:
    await schedule_service.notify_admins(text, throttle_key, interval_minutes)

def get_date_from_period(period_str, day_name):
    """Вычисляет дату конкретного дня недели на основе периода (12.01.2026-...)"""
    try:
        start_date_str = period_str.split('-')[0].strip()
        start_date = datetime.strptime(start_date_str, "%d.%m.%Y")
        
        target_day_idx = -1
        for idx, name in WEEK_DAYS_MAP.items():
            if name == day_name.upper():
                target_day_idx = idx
                break
        
        if target_day_idx != -1:
            start_weekday = start_date.weekday()
            delta = (target_day_idx - start_weekday) % 7
            target_date = start_date + timedelta(days=delta)
            return target_date.strftime("%d.%m.%Y")
            
    except Exception as e:
        logging.error("Ошибка вычисления даты: %s", e)
    return "?.?."

def format_schedule_message(day_name, period, lessons):
    """Красивое форматирование сообщения с расписанием (один день)"""
    date_str = get_date_from_period(period, day_name) if period else ""
    
    text = f"📅 <b>{day_name}</b>"
    if date_str:
        text += f" <i>({date_str})</i>"
    text += "\n"
    text += "➖➖➖➖➖➖➖➖➖➖\n"
    
    if not lessons:
        text += "🎉 <b>Пар нет!</b> <i>Можно отдыхать.</i> 🐍\n"
        return text

    for lesson in lessons:
        num = lesson[0]
        start = lesson[1]
        end = lesson[2]
        subject = lesson[3]
        room = lesson[4]
        
        num_emoji = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣"}.get(num, f"{num}️⃣")
        
        text += f"{num_emoji} <b>{start} - {end}</b>"
        if room:
            text += f"  🚪 <code>{room}</code>"
        text += "\n"
        text += f"📚 {subject}\n\n"
        
    return text

def format_week_schedule(period, schedule_data):
    """Формирует одно большое сообщение на всю неделю"""
    text = f"🆕 <b>РАСПИСАНИЕ НА НЕДЕЛЮ</b>\n"
    text += f"🗓 <b>Период:</b> {period}\n\n"
    
    for day in schedule_data:
        day_name = day['day']
        lessons = day['lessons']
        # Пропускаем дни без пар, чтобы не засорять эфир (или можно оставить)
        if not lessons:
             continue

        date_str = get_date_from_period(period, day_name)
        text += f"🔹 <b>{day_name}</b> ({date_str})\n"
        
        for lesson in lessons:
             num = lesson['num']
             subject = lesson['subject']
             room = lesson['room']
             
             text += f"   {num}. {subject}"
             if room:
                 text += f" ({room})"
             text += "\n"
        text += "\n"
        
    text += "<i>Бот автоматически будет присылать расписание на каждый день в 19:00.</i>"
    return text

schedule_updater = ScheduleUpdater(
    config, db, schedule_service, format_week_schedule=format_week_schedule
)

# --- ХЕНДЛЕРЫ ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Middleware уже пропустил только админов и зарегистрированные чаты
    chat_title = message.chat.title or (message.from_user and message.from_user.full_name) or "Чат"
    thread_id = message.message_thread_id

    await add_chat(message.chat.id, chat_title, message.chat.type, thread_id)
    
    # Убираем клавиатуру (удаляем её у пользователя)
    remove_kb = types.ReplyKeyboardRemove()
    
    await message.answer(
        f"👋 Привет! Я бот группы <b>{config.group_name}</b>.\n"
        f"Я запомнил этот канал связи.\n\n"
        "Теперь я буду работать в автоматическом режиме:\n"
        "🔹 При появлении нового расписания — скину его целиком.\n"
        "🔹 Каждый вечер в 19:00 — скину расписание на завтра.",
        reply_markup=remove_kb
    )

@dp.message(Command("today"))
async def cmd_today(message: types.Message):
    today = now_moscow()
    day_name = WEEK_DAYS_MAP.get(today.weekday())
    
    if day_name == 'ВОСКРЕСЕНЬЕ':
        await message.answer("Сегодня воскресенье, пар нет!")
        return

    period, lessons = await get_schedule_for_day(config.group_name, day_name)
    if period:
        msg = format_schedule_message(day_name, period, lessons)
        await message.answer(msg)
    else:
        await message.answer("🤷‍♂️ Расписание не найдено.")

@dp.message(Command("tomorrow"))
async def cmd_tomorrow(message: types.Message):
    tomorrow = now_moscow() + timedelta(days=1)
    day_name = WEEK_DAYS_MAP.get(tomorrow.weekday())
    
    if day_name == 'ВОСКРЕСЕНЬЕ':
        await message.answer("Завтра воскресенье! Отдыхай.")
        return

    period, lessons = await get_schedule_for_day(config.group_name, day_name)
    if period:
        msg = format_schedule_message(day_name, period, lessons)
        await message.answer(msg)
    else:
        await message.answer("🤷‍♂️ Расписание на завтра не найдено.")

@dp.message(Command("update"))
async def cmd_update(message: types.Message):
    """Принудительное обновление"""
    status_msg = await message.answer("⏳ Проверяю сайт...")
    try:
        # notify_users=False, чтобы не спамить во все группы сразу при ручном вызове
        period, schedule, _ = await check_and_update_schedule(notify_users=False, reason="manual")
        
        if period:
            await status_msg.edit_text(f"✅ <b>Обновлено!</b>")
            # Отправляем результат ТОЛЬКО в тот чат, где вызвали команду
            full_msg = format_week_schedule(period, schedule)
            await message.answer(full_msg)
        else:
            await status_msg.edit_text("🛑 Нового расписания нет.")
    except Exception as e:
        logger.error("Update error: %s", e)
        await status_msg.edit_text("❌ Ошибка.")

# --- ФОНОВЫЕ ЗАДАЧИ ---

async def broadcast_message(text: str, document_path: str | None = None) -> int:
    return await schedule_service.broadcast_message(text, document_path)

async def check_and_update_schedule(
    notify_users: bool = True, reason: str = "scheduled"
) -> tuple[Optional[str], Optional[list], bool]:
    return await schedule_updater.check_and_update(
        notify_users=notify_users, reason=reason
    )

async def daily_evening_mailing():
    tomorrow = now_moscow() + timedelta(days=1)
    target_date = tomorrow.date()
    last_sent = await get_metadata("last_daily_sent_date")
    if last_sent == target_date.isoformat():
        return

    day_name = WEEK_DAYS_MAP.get(tomorrow.weekday())
    
    if day_name == 'ВОСКРЕСЕНЬЕ':
        return

    period, lessons = await get_schedule_for_day(config.group_name, day_name)

    if period:
        msg = f"🌙 <b>Расписание на завтра:</b>\n\n"
        msg += format_schedule_message(day_name, period, lessons)
        await broadcast_message(msg)
        await set_metadata("last_daily_sent_date", target_date.isoformat())
    else:
        await notify_admins("⚠️ Не найдено расписание для вечерней рассылки.", "daily_missing", 180)

async def safe_check_schedule() -> None:
    try:
        await check_and_update_schedule(notify_users=True, reason="scheduled")
    except Exception as e:
        logger.exception("Ошибка плановой проверки расписания: %s", e)
        await notify_admins("❌ Ошибка плановой проверки расписания.", "scheduled_error", 60)

async def safe_daily_mailing() -> None:
    try:
        await daily_evening_mailing()
    except Exception as e:
        logger.exception("Ошибка вечерней рассылки: %s", e)
        await notify_admins("❌ Ошибка вечерней рассылки.", "daily_error", 60)

async def on_startup(bot: Bot):
    await init_db()
    await startup_recovery()

async def startup_recovery() -> None:
    try:
        await check_and_update_schedule(notify_users=True, reason="startup")
    except Exception as e:
        logger.exception("Ошибка восстановления при старте: %s", e)
        await notify_admins("❌ Ошибка восстановления при старте.", "startup_error", 60)
        return
    
    now = now_moscow()
    if now.hour < DAILY_MAILING_HOUR:
        return
    
    tomorrow = now + timedelta(days=1)
    day_name = WEEK_DAYS_MAP.get(tomorrow.weekday())
    if day_name == 'ВОСКРЕСЕНЬЕ':
        return
    
    last_sent = await get_metadata("last_daily_sent_date")
    if last_sent != tomorrow.date().isoformat():
        await daily_evening_mailing()

async def on_shutdown(bot: Bot) -> None:
    scheduler.shutdown(wait=True)
    await db.close()
    logger.info("Бот корректно остановлен")

async def create_app() -> tuple[Bot, Dispatcher]:
    """Инициализирует зависимости (БД, сервисы) и возвращает bot, dp."""
    await init_db()
    return bot, dp


async def main() -> None:
    bot, dp = await create_app()
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    scheduler.add_job(
        safe_check_schedule, "interval", hours=CHECK_INTERVAL_HOURS
    )
    scheduler.add_job(
        safe_daily_mailing,
        "cron",
        hour=DAILY_MAILING_HOUR,
        minute=DAILY_MAILING_MINUTE,
    )

    scheduler.start()
    await dp.start_polling(bot)
