"""Точка входа: загрузка конфигурации и запуск бота."""
import asyncio

from dotenv import load_dotenv

load_dotenv()

from bot import main

if __name__ == "__main__":
    asyncio.run(main())
