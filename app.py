import asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from config import Config
from db import init_engine, create_all
import models  # для create_all
from handlers import onboarding, commands, errors
from scheduler import BotScheduler

async def main():
    load_dotenv()
    cfg = Config.load()
    if not cfg.bot_token:
        raise RuntimeError("BOT_TOKEN не задано")

    init_engine(cfg.database_url)
    await create_all(models)

    bot = Bot(token=cfg.bot_token, default=DefaultBotProperties(parse_mode=None))
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(onboarding.router)
    dp.include_router(commands.router)
    dp.include_router(errors.router)

    bs = BotScheduler(bot)
    bs.start()

    print("Bot started.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
