import asyncio
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path('/root/raspisanie_bot_ait/.env'))

from bot import weekly_preview_mailing
from database import init_db, db


async def main():
    await init_db()
    try:
        await weekly_preview_mailing()
    finally:
        await db.close()

    conn = sqlite3.connect('/root/raspisanie_bot_ait/bot_database.db')
    cur = conn.cursor()
    print('---SUNDAY_METADATA---')
    for key in ('last_sunday_weekly_sent_period', 'last_weekly_sent_period'):
        cur.execute('select value from metadata where key=?', (key,))
        print(key, cur.fetchone())
    print('---PIN_KEYS---')
    for row in cur.execute("select key, value from metadata where key like 'pinned_schedule_message:%' order by key"):
        print(row)
    conn.close()


asyncio.run(main())
